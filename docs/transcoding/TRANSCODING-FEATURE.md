# Support for transcoding using 

## Configuiration
There should be a settings panel to support necessary configuration for ffprobe and ffmpeg. Including file locations. Best practices for parameters should be available - not everything.. 

There should be a way to try to find the files in the path or manually enter the path.

There should be default configuration for folder location for converted files

The transcoded target path should be shown as a location as well - BUT ONLY if there are anything in it. If there are things in it, it should show at the left side below "Library" and "Downloads". It will should have two sub folders for "Movies" and "Series" (automatically). 

## Sample script

Use this script as input: docs/transcoding/sample-script.cmd
It should point to the direction we want to go with this feature.


## Way of working

SHould be possible to convert a file from the the lists. So if some movies are selected, there should be a button "transcode" at the bottom left (like move and delete). 

Transcoding should be started as a backgeround proces.. Shown with status and progress at the top. When clicking on the status/progress indicator for transcoding it open a dialog with the console output of transcoding. There should be a button to "kill" the transcoding process.

If transcoding is ongoing IT MUST NOT BE POSSIBLE TO START MORE TRANSCODING !!

When starting transcode with one or more files, it should show the configured options. It should ask if the files should be probed first and show information to the user according to the output of the probe. User should not be able to convert without having activated probing first. 

When transcoding it should keep the folder structure in the new place for both movies and series (and their episodes). 

After successful transcoding there will automatically be a duplicate - but without NFO necessarily (Jellyfin might not see the transcoded files). When going into the "Duplicates" view, at the top there should be an option next to "Select all" called "Select transcoded". This should only show duplicates that comes due to transcoding.. 

When comparing a transcoded duplicate there should be a button at the bottom.. "Use transcoded video"... It should warn the user to be sure. And then it should overwrite the video ONLY (not NFO) and delete the transcoded moviefile and folder afterwards so there are no duplicate anymore.


