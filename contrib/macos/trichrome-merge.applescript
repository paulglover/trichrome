(*
	Trichrome Merge — a droplet that hands a selection of RAW frames to the
	trichrome command-line tool.

	Built as an .app (see build-app.sh) it becomes something digiKam, Finder or
	Path Finder can "Open With": select the frames of a shoot, open them with
	this, and every consecutive triplet is merged.

	It deliberately does NO validation of its own. trichrome already checks that
	the batch is a multiple of three, that every file is a supported RAW, and
	that the three frames of a triplet came from the same sensor — and its
	messages are written to be read by a person. Duplicating any of that here
	would only create a second place for the rules to drift. This script finds
	the tool, builds the command, and shows you what the tool said.

	Selection ORDER does not matter: trichrome sorts the batch by filename
	before grouping, so whatever order digiKam hands the files over in, the
	triplets come out the same.
*)

-- Set this to an absolute path to skip auto-detection entirely.
property trichromePath : ""

-- Where to look, in order, before falling back to asking a login shell.
property searchPaths : {"/opt/homebrew/bin/trichrome", "/usr/local/bin/trichrome", "/opt/local/bin/trichrome"}

-- Ask for the output format on every run. Set to false to always use
-- defaultFormat without prompting.
property askForFormat : true
property defaultFormat : "dng" -- "dng" or "tiff"

-- DESTRUCTIVE. With this on, the source RAWs are permanently deleted once
-- their merged file is written and verified. A confirmation is shown first —
-- a stray double-click on a droplet should not be able to delete a shoot.
property deleteOriginals : false

-- Offer a "Show in Finder" button when the merge succeeds.
property revealResult : true


on run
	-- Double-clicked rather than opened with files: ask for them.
	set chosen to choose file with prompt ¬
		"Choose the RAW frames to merge — three per shot, in filename order." ¬
		with multiple selections allowed
	my mergeItems(chosen)
end run


on open droppedItems
	-- Dropped on the icon, or sent by another app's "Open With".
	my mergeItems(droppedItems)
end open


on mergeItems(theItems)
	if (count of theItems) is 0 then return

	set toolPath to my findTrichrome()
	if toolPath is missing value then return

	set theFormat to my chooseFormat()
	if theFormat is missing value then return

	if deleteOriginals then
		if not my confirmDeletion(count of theItems) then return
	end if

	set posixPaths to {}
	repeat with anItem in theItems
		set end of posixPaths to POSIX path of anItem
	end repeat

	try
		display notification "Merging " & (count of posixPaths) & " frame(s)…" ¬
			with title "Trichrome"
	end try

	set theCommand to my buildCommand(toolPath, theFormat, posixPaths)
	try
		set theOutput to do shell script theCommand
	on error errorMessage number errorNumber
		if errorNumber is -128 then return
		-- stderr is folded into stdout, so a non-zero exit carries trichrome's
		-- own message — which is the thing worth showing.
		display alert "Merge failed" message errorMessage as critical
		return
	end try

	my reportSuccess(theOutput)
end mergeItems


on buildCommand(toolPath, theFormat, posixPaths)
	(* The shell command, with every path quoted. Kept separate from the UI so
	   it can be exercised without a screen; build-app.sh calls it directly as a
	   self-test after compiling. *)
	set theCommand to quoted form of toolPath & " merge --format " & theFormat
	if deleteOriginals then set theCommand to theCommand & " --delete-originals"
	repeat with aPath in posixPaths
		set theCommand to theCommand & " " & quoted form of (aPath as text)
	end repeat
	return theCommand & " 2>&1"
end buildCommand


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


on chooseFormat()
	if not askForFormat then return defaultFormat
	try
		set theButton to button returned of (display dialog ¬
			"Merge into which format?" & return & return & ¬
			"DNG opens through a converter's RAW pipeline — raw white balance, " & ¬
			"exposure before the tone curve. Uncompressed, so larger." & return & return & ¬
			"TIFF is compressed, universally readable, and opens as a rendered image." ¬
			buttons {"Cancel", "TIFF", "DNG"} default button "DNG" ¬
			with title "Trichrome Merge")
	on error number -128
		return missing value -- a button literally named Cancel raises this
	end try
	if theButton is "TIFF" then return "tiff"
	if theButton is "DNG" then return "dng"
	return missing value
end chooseFormat


on confirmDeletion(fileCount)
	try
		set theButton to button returned of (display alert ¬
			"Delete the source RAWs after merging?" message ¬
			"This droplet is configured with deleteOriginals set to true. Each " & ¬
			"triplet's " & fileCount & " source file(s) will be permanently " & ¬
			"deleted once its merged file has been written and verified." ¬
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
	(* trichrome reports each result as `wrote /some/path  (WxH, uint16)`. *)
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
