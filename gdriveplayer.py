# -*- coding: utf-8 -*-

import os
import sqlite3
import subprocess
from argparse import ArgumentParser
from collections import namedtuple
from logging import getLogger
from tempfile import TemporaryDirectory
from tqdm import tqdm
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.service_account import Credentials

logger = getLogger(__name__)

_dirname = os.path.abspath(os.path.dirname(__file__))
_credentialjson = os.path.join(_dirname, "_credentials.json")
_dbfile = os.path.join(_dirname, "_gdriveplayer.db")


AudioFile = namedtuple("AudioFile", "id name mimetype parent size md5checksum")


def create_api_service():
    jsonfile = _credentialjson
    #creds = ServiceAccountCredentials.from_json_keyfile_name(jsonfile)
    if os.path.isfile(jsonfile):
        # local json file
        creds = Credentials.from_service_account_file(jsonfile)
        service = build('drive', 'v3', credentials=creds)
    else:
        service = build('drive', 'v3')
    
    return service

def _get_sql(query):
    with sqlite3.connect(_dbfile) as conn:
        c = conn.cursor()
        c.execute(query)
        for row in c:
            yield row

def _exec_sql(query, value=None, values=None):
    assert value is None or values is None
    with sqlite3.connect(_dbfile) as conn:
        c = conn.cursor()
        if values is not None:
            c.executemany(query, values)
        elif value is not None:
            c.execute(query, value)
        else:
            c.execute(query)
        conn.commit()
    

def create_database(replace=False):
    if replace and os.path.isfile(_dbfile):
        os.unlink(_dbfile)

    with sqlite3.connect(_dbfile) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS audiofiles (
           id          TEXT UNIQUE PRIMARY KEY
          ,name        TEXT
          ,mimetype    TEXT
          ,parent      TEXT
          ,size        INTEGER
          ,md5checksum TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS audiometa (
           id          TEXT UNIQUE PRIMARY KEY 
          ,duration    INTEGER
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS folders (
           id          TEXT UNIQUE PRIMARY KEY
          ,folder      TEXT
          ,prefix      TEXT
        )
        """)
        conn.commit()

def update_audio_database(replace=False):
    create_database(replace=replace)
    with sqlite3.connect(_dbfile) as conn:
        c = conn.cursor()
        placeholder = ",".join("?" * len(AudioFile._fields))
        q = "INSERT INTO audiofiles VALUES ({})".format(placeholder)
        c.executemany(q, tqdm(search_audio_files()))
        conn.commit()
            
def search_audio_files():
    service = create_api_service()

    page_token = None
    if os.path.isfile("data.json"): os.unlink("data.json")
    i = 0
    while True:
        response = service.files().list(
            q="mimeType contains 'audio'",
            spaces='drive',
            pageSize=1000,
            fields='nextPageToken, files(id, name, mimeType, parents, size, md5Checksum)',
            pageToken=page_token
        ).execute()
        files = response.get('files', [])
        for file in files:
            # parents is a list at maximum one element
            # we validate that and extract the element
            parents = file.get("parents", [])
            assert len(parents) <= 1
            parent = None if len(parents) == 0 else parents[0]
            if "parents" in file:
                del file["parents"]
            file["parent"] = parent
            # make keys to lower case
            file = {key.lower():value for key,value in file.items()}
            # add missing attributes
            for field in AudioFile._fields:
                if field not in file:
                    file[field] = None
            a = AudioFile(**file)
            yield a
            
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break

def play():
    files = [(row[0], row[1]) for row in _get_sql("SELECT id, name FROM audiofiles ORDER BY random()")]
    #print(ids)
    with TemporaryDirectory() as tmpdir:
        for (id, name) in files:
            try:
                _play_one(id, name, tmpdir)
            except Exception as e:
                logger.warning("Failed to play '%s' (%s) due to the error:\n%s", name, id, e)

def _play_one(id, name, tmpdir):
    # download file
    service = create_api_service()
    request = service.files().get_media(fileId=id)
    filepath = os.path.join(tmpdir, name)
    with open(filepath, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            print("Download %d%%." % int(status.progress() * 100))
    
    #command = ["ffplay", filepath, "-autoexit", "-nodisp"]
    #p = subprocess.run(command, stdin=subprocess.PIPE)
    command = ["mplayer", filepath]
    p = subprocess.run(command)
    

def main():
    parser = ArgumentParser(description="Play music files in google drive")
    parser.add_argument("-U", "--update-list", action="store_true", help="Update file list")

    args = parser.parse_args()

    if args.update_list or not os.path.isfile(_dbfile):
        update_audio_database(replace=True)

    play()

if __name__ == "__main__":
    main()