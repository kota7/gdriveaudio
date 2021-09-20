gdriveaudio
===================
[![](https://badge.fury.io/py/gdriveaudio.svg)](https://badge.fury.io/py/gdriveaudio)

Play audio files in the google drive.

## Requirments

- Python3
- MPlayer and FFmpeg
  - Ubuntu: `sudo apt install mplayer ffmpeg`
  - MacOSX: `brew install mplayer ffmpeg`
  - Windows: Follow official instructionat [MPlayer](http://www.mplayerhq.hu/design7/dload.html) and [FFmpeg](https://ffmpeg.org/download.html)
- Google account, access to the google cloud platform

## Set up with Google cloud

### Enable google drive API

- Log in to the google cloud console and create or choose the project to use.
- Go to [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com) page and enable it.

### Create a service account to access google drive

- Log in to the google cloud console and create or choose the project to use.
- Go to [IAM & Admin > Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).
- Click "CREATE SERVICE ACCOUNT".
- Type in an arbitrary service account name (e.g. `gdriveaudio-sa`).
- Click "DONE".
- Note the email address of the service account name (e.g.` gdriveaudio-sa@gdriveaudio-project.iam.gserviceaccount.com`).

### Give the service account the access to the music folder

- Open [Google Drive](https://drive.google.com/).
- Open the folder that contains music files.
- Click the folder name and choose "Share".
- Paste the email address of the service account noted in the previous step.
- Click "Done".

### Create the credentials file of the service account

- Log in to the google cloud console and choose the project to use.
- Open [IAM & Admin > Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).
- Click the service account created above.
- Click "KEYS" in the top menu.
- Click "ADD KEY" and then "Create new key"
- Choose "JSON" key type and click "CREATE".
- Rename the downloaded file as `_credentials.json` and move to a folder to use for this tool.

## Install

```shell
# from pypi
$ pip3 install gdriveaudio

# from github
$ git clone --depth=1 https://github.com/kota7/gdriveaudio.git
$ pip3 install -U ./gdriveaudio
```

## Start playing

For the program to locate the credential file, either
- Run the commands in the folder that has the "_credentials.json"
- Set `GDRIVEAUDIO_DIRECTORY` environment variable as the directory containing "_credentials.json", or
- Run the commands with `--workdir={directory}` option to specify the directory containing "_credentials.json"

```shell
# Initialize data
#   Delete pre-fetched data
#   Create SQLite database file '_gdriveaudio.db'
$ gdriveaudio init

# Update data
#   -U for updating file list
#   -F for updating folder structure information
#   -M for updating audio metadata (this can take hours)
$ gdriveaudio update -UF
# or
$ gdriveaudio update -UFM

# Play all files
$ gdriveaudio play 
# Play files with some condition
#   -k: case-insensitive keywords to search
#   -K: case-sensitive keywords to search
#   -q: Query inside 'WHERE' condition (follow SQLite dialect)
$ gdriveaudio play -k "beethoven"
$ gdriveaudio play -K "Michael J"       # case sensitive search
$ gdriveaudio play -k "name:lucky"      # search only inside 'name' field
$ gdriveaudio play -q "duration > 600"  # 10+ min only

# Show data
# -k, -K, -q filters also work
$ gdriveaudio data -n 5
$ gdriveaudio data -n 10 -k "beethoven"

# See full command options
$ gdriveaudio -h
$ gdriveaudio {init,update,play,data} -h
```

The audio player can be controled by key strokes (See full description at `man mplayer`):

```shell
 <-  or  ->       seek backward/forward 10 seconds
 down or up       seek backward/forward  1 minute
 pgdown or pgup   seek backward/forward 10 minutes
 < or >           step backward/forward in playlist
 p or SPACE       pause movie (press any key to continue)
 q or ESC         stop playing and quit program
 + or -           adjust audio delay by +/- 0.1 second
 o                cycle OSD mode:  none / seekbar / seekbar + timer
 * or /           increase or decrease PCM volume
 x or z           adjust subtitle delay by +/- 0.1 second
 r or t           adjust subtitle position up/down, also see -vf expand
```