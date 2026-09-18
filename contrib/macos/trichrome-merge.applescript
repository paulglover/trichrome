(*
	Trichrome Merge — a droplet that hands the three RAW frames of one shot to
	the trichrome command-line tool.

	Built as an .app (see build-app.sh) it becomes something digiKam, Finder or
	Path Finder can "Open With": select the three frames of a shot, open them
	with this, give it the film ID, and they are merged into one linear DNG
	named FILMID.dng.

	WHAT IT ASKS
	------------
	Once the selection is gathered, one dialog asks for:

	  * the film ID — the merged file's name, and its XMP dc:identifier. Asked
	    fresh every time, because it is different for every shot. Merge stays
	    disabled until one is typed.
	  * "Different output folder", and the folder. Unticked, the merge is
	    written beside its first frame. Both are remembered between runs, in
	    the app's own preferences (net.trichrome.merge-droplet) rather than in
	    script properties: an applet that saves its properties rewrites its own
	    bundle, which breaks the signature build-app.sh applied. A new install
	    starts unticked, with no folder.

	WHY THIS APP STAYS OPEN, AND WHY IT WAITS A SECOND
	--------------------------------------------------
	LaunchServices does not always deliver a multiple-file selection as one
	event: a selection can arrive as two `odoc` events about 25 milliseconds
	apart — say files 2 and 3 first, then file 1 on its own. A droplet that
	acted on each event as it arrived would see two files, then one, and
	refuse both — for a selection that was exactly right.

	So this is a STAY-OPEN applet. `on open` only accumulates; the `idle`
	handler waits for the deliveries to stop and then runs the tool ONCE on
	everything that arrived. One second of quiet is forty times the observed
	gap, and the app quits as soon as the merge is done.

	The same wait applies to a plain double-click: `on run` cannot know whether
	files are about to follow it, so it only raises a flag, and the idle handler
	shows the file chooser if nothing arrives.

	Beyond a selection that is not exactly three files, a blank film ID, or a
	ticked box with no folder, it does NO validation of its own. trichrome
	already checks that every file is a supported RAW, that the frames came
	from the same sensor, that the film ID is a usable file name, and that
	nothing is about to be written over — and its messages are written to be
	read by a person. Duplicating any of that here would only create a second
	place for the rules to drift.

	Selection ORDER does not matter: trichrome takes the frames in filename
	order, whatever order they are handed over in.
*)

use AppleScript version "2.5"
use framework "Foundation"
use framework "AppKit"
use scripting additions

-- Set this to an absolute path to skip auto-detection entirely.
property trichromePath : ""

-- Where to look, in order, before falling back to asking a login shell.
property searchPaths : {"/opt/homebrew/bin/trichrome", "/usr/local/bin/trichrome", "/opt/local/bin/trichrome"}

-- DESTRUCTIVE. With this on, the source RAWs are permanently deleted once
-- their merged file is written and verified. A confirmation is shown first —
-- a stray double-click on a droplet should not be able to delete a shot.
property deleteOriginals : false

-- Offer a "Show in Finder" button when the merge succeeds.
property revealResult : true

-- How the selection is gathered. `idleSeconds` is how often the idle handler
-- runs; `quietTicks` is how many idle rounds with nothing new must pass before
-- the selection is considered complete. 0.5 x 2 = one second of quiet.
property idleSeconds : 0.5
property quietTicks : 2

-- Accumulated between deliveries. Reset on every launch as well as declared
-- empty, because an applet can persist property values into its own bundle.
property pendingItems : {}
property ticksSinceChange : 0
property awaitingChoice : false

-- The settings dialog's controls, held only while it is on screen so its
-- button actions can reach them. Always cleared afterwards: an applet cannot
-- save a property that still holds an Objective-C object when it quits.
property settingsCheckbox : missing value
property settingsPathField : missing value
property settingsChooseButton : missing value
property settingsMergeButton : missing value


on run
	-- A double-click, OR the moment before files arrive: it cannot be told
	-- apart yet, so only raise a flag and let idle decide.
	set pendingItems to {}
	set ticksSinceChange to 0
	set awaitingChoice to true
end run


on open droppedItems
	-- Dropped on the icon, or sent by another app's "Open With". Only
	-- accumulate: more of the same selection may be milliseconds away.
	set awaitingChoice to false
	my absorbItems(droppedItems)
end open


on idle
	if (count of pendingItems) > 0 then
		set ticksSinceChange to ticksSinceChange + 1
		if ticksSinceChange < quietTicks then return idleSeconds
		my mergeItems(my takeBatch())
		quit
		return idleSeconds
	end if

	if awaitingChoice then
		set ticksSinceChange to ticksSinceChange + 1
		if ticksSinceChange < quietTicks then return idleSeconds
		-- Nothing followed the launch, so it really was a double-click.
		set awaitingChoice to false
		try
			set chosen to choose file with prompt ¬
				"Choose the three RAW frames of one shot to merge." ¬
				with multiple selections allowed
		on error number -128
			quit
			return idleSeconds
		end try
		my mergeItems(chosen)
		quit
	end if
	return idleSeconds
end idle


on absorbItems(newItems)
	(* Add a delivery to the batch and restart the quiet countdown. Separated
	   from the event handler so build-app.sh can exercise the coalescing
	   without needing a screen. *)
	set pendingItems to pendingItems & newItems
	set ticksSinceChange to 0
	return count of pendingItems
end absorbItems


on takeBatch()
	(* Hand over everything accumulated so far and start again empty. *)
	set theBatch to pendingItems
	set pendingItems to {}
	set ticksSinceChange to 0
	return theBatch
end takeBatch


on mergeItems(theItems)
	if (count of theItems) is 0 then return

	set problem to my selectionProblem(count of theItems)
	if problem is not "" then
		activate
		display alert "Select exactly three frames" message problem as warning
		return
	end if

	set toolPath to my findTrichrome()
	if toolPath is missing value then return

	set posixPaths to {}
	repeat with anItem in theItems
		set end of posixPaths to POSIX path of anItem
	end repeat

	set theSettings to my askSettings(count of posixPaths)
	if theSettings is missing value then return
	set {filmId, outDir} to theSettings

	if deleteOriginals then
		if not my confirmDeletion(count of posixPaths) then return
	end if

	try
		display notification "Merging " & (count of posixPaths) & " frames as " & ¬
			filmId & "…" with title "Trichrome"
	end try

	set theCommand to my buildCommand(toolPath, filmId, outDir, posixPaths)
	try
		set theOutput to do shell script theCommand
	on error errorMessage number errorNumber
		if errorNumber is -128 then return
		-- stderr is folded into stdout, so a non-zero exit carries trichrome's
		-- own message — which is the thing worth showing.
		activate
		display alert "Merge failed" message errorMessage as critical
		return
	end try

	my reportSuccess(theOutput)
end mergeItems


on buildCommand(toolPath, filmId, outDir, posixPaths)
	(* The shell command, with every argument quoted. `outDir` is "" for
	   "beside the first frame". The options are written as --opt=value, so a
	   film ID that starts with a hyphen is still a value and not an option.
	   Kept separate from the UI so it can be exercised without a screen;
	   build-app.sh calls it directly as a self-test after compiling. *)
	set theCommand to quoted form of toolPath
	set theCommand to theCommand & " " & quoted form of ("--filmid=" & filmId)
	if outDir is not "" then ¬
		set theCommand to theCommand & " " & quoted form of ("--out=" & outDir)
	if deleteOriginals then set theCommand to theCommand & " --delete-originals"
	repeat with aPath in posixPaths
		set theCommand to theCommand & " " & quoted form of (aPath as text)
	end repeat
	return theCommand & " 2>&1"
end buildCommand


on selectionProblem(itemCount)
	(* Why a selection of `itemCount` files cannot be merged, or "" when it
	   can. Said before the settings dialog, so nobody types a film ID for a
	   merge that was never going to run. The tool says the same, but only
	   after. *)
	if itemCount is 3 then return ""
	if itemCount is 1 then
		set counted to "1 file was"
	else
		set counted to (itemCount as text) & " files were"
	end if
	return counted & " selected. Trichrome merges exactly three: the frames of one shot, one per light. Select those three and open them together."
end selectionProblem


on askSettings(fileCount)
	(* The film ID and output folder for this merge, as {filmId, outDir} with
	   outDir "" for "beside the first frame" — or missing value if the user
	   cancelled. The output-folder choice is saved only when the user clicks
	   Merge, so cancelling leaves the previous choice as it was. *)
	set {useOutDir, outDir} to my loadSettings()
	set filmId to ""
	set problem to ""
	repeat
		set theAnswer to my settingsDialog(fileCount, filmId, useOutDir, outDir, problem)
		if theAnswer is missing value then return missing value
		set {filmId, useOutDir, outDir} to theAnswer
		set problem to my settingsProblem(filmId, useOutDir, outDir)
		if problem is "" then exit repeat
	end repeat
	my saveSettings(useOutDir, outDir)
	if not useOutDir then set outDir to ""
	return {filmId, outDir}
end askSettings


on settingsProblem(filmId, useOutDir, outDir)
	(* Why these settings cannot be used, or "" when they can. Only what the
	   dialog alone can know; everything else is trichrome's to say. *)
	if filmId is "" then return "Enter a film ID — it names the merged file."
	if useOutDir and outDir is "" then ¬
		return "Choose an output folder, or untick “Different output folder”."
	return ""
end settingsProblem


on loadSettings()
	(* {useOutDir, outDir} as last saved; unticked and empty on a new install. *)
	set theDefaults to current application's NSUserDefaults's standardUserDefaults()
	set useOutDir to (theDefaults's boolForKey:"useOutputFolder") as boolean
	set outDir to theDefaults's stringForKey:"outputFolder"
	if outDir is missing value then return {useOutDir, ""}
	return {useOutDir, outDir as text}
end loadSettings


on saveSettings(useOutDir, outDir)
	set theDefaults to current application's NSUserDefaults's standardUserDefaults()
	theDefaults's setBool:useOutDir forKey:"useOutputFolder"
	theDefaults's setObject:outDir forKey:"outputFolder"
end saveSettings


on settingsDialog(fileCount, filmId, useOutDir, outDir, problem)
	(* One alert holding every setting: a film ID field, the checkbox, and the
	   folder with a Choose… button. Returns {filmId, useOutDir, outDir},
	   trimmed, or missing value for Cancel. `problem`, when not "", is shown in
	   place of the usual explanation — it is why the dialog is being asked
	   again. *)
	set theView to current application's NSView's alloc()'s initWithFrame:{{0, 0}, {380, 100}}

	set idLabel to current application's NSTextField's labelWithString:"Film ID:"
	idLabel's setFrame:{{0, 75}, {60, 20}}
	set idField to current application's NSTextField's alloc()'s initWithFrame:{{64, 72}, {316, 24}}
	idField's setStringValue:filmId
	idField's setDelegate:me

	set settingsCheckbox to current application's NSButton's checkboxWithTitle:"Different output folder" target:me action:"outputFolderToggled:"
	settingsCheckbox's setFrame:{{0, 40}, {380, 20}}
	if useOutDir then
		settingsCheckbox's setState:1
	else
		settingsCheckbox's setState:0
	end if

	set settingsPathField to current application's NSTextField's alloc()'s initWithFrame:{{0, 6}, {284, 24}}
	settingsPathField's setStringValue:outDir
	settingsPathField's setPlaceholderString:"Output folder"
	settingsPathField's setUsesSingleLineMode:true
	(settingsPathField's cell())'s setLineBreakMode:(current application's NSLineBreakByTruncatingHead)
	set settingsChooseButton to current application's NSButton's buttonWithTitle:"Choose…" target:me action:"chooseOutputFolder:"
	settingsChooseButton's setFrame:{{290, 2}, {90, 32}}
	my syncOutputFolderControls()

	repeat with aControl in {idLabel, idField, settingsCheckbox, settingsPathField, settingsChooseButton}
		(theView's addSubview:aControl)
	end repeat

	set theAlert to current application's NSAlert's alloc()'s init()
	theAlert's setMessageText:("Merge " & fileCount & " frames into one DNG")
	if problem is "" then
		theAlert's setInformativeText:"The merged file is named after its film ID. Unless a different output folder is chosen, it is written beside the first frame."
	else
		theAlert's setInformativeText:problem
	end if
	theAlert's setAccessoryView:theView
	set settingsMergeButton to theAlert's addButtonWithTitle:"Merge"
	theAlert's addButtonWithTitle:"Cancel"
	my syncMergeButton(idField)
	theAlert's layout()
	(theAlert's |window|())'s setInitialFirstResponder:idField

	activate
	set theResponse to (theAlert's runModal()) as integer
	set theAnswer to {my trimmed(idField's stringValue()), ¬
		((settingsCheckbox's state()) as integer) is 1, ¬
		my trimmed(settingsPathField's stringValue())}

	set settingsCheckbox to missing value
	set settingsPathField to missing value
	set settingsChooseButton to missing value
	set settingsMergeButton to missing value

	if theResponse is not (current application's NSAlertFirstButtonReturn) as integer then ¬
		return missing value
	return theAnswer
end settingsDialog


on controlTextDidChange:aNotification
	(* The film ID field's delegate: re-checked on every keystroke. *)
	my syncMergeButton(aNotification's object())
end controlTextDidChange:


on syncMergeButton(idField)
	(* Merge is only clickable once there is a film ID. Whitespace alone does
	   not count, since it is trimmed away. *)
	settingsMergeButton's setEnabled:((my trimmed(idField's stringValue())) is not "")
end syncMergeButton


on outputFolderToggled:sender
	my syncOutputFolderControls()
end outputFolderToggled:


on syncOutputFolderControls()
	(* The folder is only editable while the box is ticked. It is kept, not
	   cleared, when the box is unticked, so ticking it again restores it. *)
	set isOn to ((settingsCheckbox's state()) as integer) is 1
	settingsPathField's setEnabled:isOn
	settingsChooseButton's setEnabled:isOn
end syncOutputFolderControls


on chooseOutputFolder:sender
	set thePanel to current application's NSOpenPanel's openPanel()
	thePanel's setCanChooseFiles:false
	thePanel's setCanChooseDirectories:true
	thePanel's setCanCreateDirectories:true
	thePanel's setAllowsMultipleSelection:false
	thePanel's setPrompt:"Choose"
	thePanel's setMessage:"Choose the folder to write merged files into."
	set startPath to my trimmed(settingsPathField's stringValue())
	if startPath is not "" then ¬
		thePanel's setDirectoryURL:(current application's NSURL's fileURLWithPath:((current application's NSString's stringWithString:startPath)'s stringByExpandingTildeInPath()))
	if ((thePanel's runModal()) as integer) is (current application's NSModalResponseOK) as integer then
		settingsPathField's setStringValue:((thePanel's |URL|())'s |path|())
	end if
end chooseOutputFolder:


on trimmed(theText)
	(* theText, an AppleScript or Cocoa string, without surrounding whitespace. *)
	set theString to current application's NSString's stringWithString:theText
	return (theString's stringByTrimmingCharactersInSet:(current application's NSCharacterSet's whitespaceAndNewlineCharacterSet())) as text
end trimmed


on findTrichrome()
	(* `do shell script` runs with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin),
	   which is never where trichrome is installed. So: the configured path, then
	   the usual package-manager prefixes, then a LOGIN shell — which is what
	   finds it inside a venv, pyenv or conda environment. *)
	if trichromePath is not "" then
		if my isExecutable(trichromePath) then return trichromePath
	end if
	repeat with aPath in searchPaths
		if my isExecutable(aPath as text) then return (aPath as text)
	end repeat
	try
		set found to do shell script "$SHELL -lc 'command -v trichrome' 2>/dev/null"
		if found is not "" then return found
	end try

	activate
	display alert "trichrome not found" message ¬
		"This droplet could not find the trichrome command." & return & return & ¬
		"Install it with `pip install -e .` in the trichrome checkout, then " & ¬
		"either put it on your login shell's PATH or open this script and set " & ¬
		"the trichromePath property to its absolute path." as critical
	return missing value
end findTrichrome


on isExecutable(aPath)
	try
		do shell script "test -x " & quoted form of aPath
		return true
	on error
		return false
	end try
end isExecutable


on confirmDeletion(fileCount)
	activate
	try
		set theButton to button returned of (display alert ¬
			"Delete the source RAWs after merging?" message ¬
			"This droplet is configured with deleteOriginals set to true. The " & ¬
			fileCount & " source files will be permanently deleted once the " & ¬
			"merged file has been written and verified." ¬
			as critical buttons {"Cancel", "Merge and delete"} default button "Cancel")
	on error number -128
		return false
	end try
	return theButton is "Merge and delete"
end confirmDeletion


on reportSuccess(theOutput)
	set writtenPaths to my writtenPathsFrom(theOutput)
	set theButtons to {"OK"}
	if revealResult and (count of writtenPaths) > 0 then ¬
		set theButtons to {"Show in Finder", "OK"}

	activate
	set theButton to button returned of (display dialog theOutput ¬
		buttons theButtons default button (item -1 of theButtons) ¬
		with title "Trichrome Merge")

	if theButton is "Show in Finder" then
		tell application "Finder"
			reveal (POSIX file (item 1 of writtenPaths) as alias)
			activate
		end tell
	end if
end reportSuccess


on writtenPathsFrom(theOutput)
	(* trichrome reports its result as `wrote /some/path  (WxH, uint16)`.
	   tests/test_cli.py pins that line's shape, because this parses it. *)
	set thePaths to {}
	repeat with aLine in paragraphs of theOutput
		set aLine to aLine as text
		if aLine starts with "wrote " then
			set aPath to text 7 thru -1 of aLine
			set savedDelimiters to AppleScript's text item delimiters
			set AppleScript's text item delimiters to "  ("
			set aPath to text item 1 of aPath
			set AppleScript's text item delimiters to savedDelimiters
			set end of thePaths to aPath
		end if
	end repeat
	return thePaths
end writtenPathsFrom
