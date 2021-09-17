# -*- coding: utf-8 -*-

import os
import sqlite3
import subprocess
from argparse import ArgumentParser
from collections import namedtuple
from logging import getLogger, basicConfig
from tempfile import TemporaryDirectory
from tqdm import tqdm
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.service_account import Credentials

logger = getLogger(__name__)


class config:
    credentialjson = os.path.join(os.getcwd(), "_credentials.json")
    dbfile = os.path.join(os.getcwd(), "_gdriveplayer.db")


AudioFile = namedtuple("AudioFile", "id name mimetype parent size md5checksum")


def create_api_service():
    jsonfile = config.credentialjson
    #creds = ServiceAccountCredentials.from_json_keyfile_name(jsonfile)
    if os.path.isfile(jsonfile):
        # local json file
        creds = Credentials.from_service_account_file(jsonfile)
        service = build('drive', 'v3', credentials=creds)
    else:
        # default setting, does not seem working for now
        service = build('drive', 'v3')
    return service

def _get_sql(query):
    with sqlite3.connect(config.dbfile) as conn:
        c = conn.cursor()
        c.execute(query)
        for row in c:
            yield row

def _exec_sql(query, value=None, values=None):
    assert value is None or values is None
    with sqlite3.connect(config.dbfile) as conn:
        c = conn.cursor()
        if values is not None:
            c.executemany(query, values)
        elif value is not None:
            c.execute(query, value)
        else:
            c.execute(query)
        conn.commit()
    

def create_database(replace=False):
    if replace and os.path.isfile(config.dbfile):
        os.unlink(config.dbfile)
    
    _exec_sql("""
    CREATE TABLE IF NOT EXISTS audiofiles (
        id          TEXT UNIQUE PRIMARY KEY
        ,name        TEXT
        ,mimetype    TEXT
        ,parent      TEXT
        ,size        INTEGER
        ,md5checksum TEXT
    )
    """)

    _exec_sql("""
    CREATE TABLE IF NOT EXISTS audiometa (
        id          TEXT UNIQUE PRIMARY KEY 
        ,duration    INTEGER
    )
    """)

    _exec_sql("""
    CREATE TABLE IF NOT EXISTS folders (
        id          TEXT UNIQUE PRIMARY KEY
        ,folder      TEXT
        ,prefix      TEXT
    )
    """)
    

def update_audio_database(replace=False):
    create_database(replace=replace)
    placeholder = ",".join("?" * len(AudioFile._fields))
    q = "INSERT INTO audiofiles VALUES ({})".format(placeholder)
    _exec_sql(q, values=tqdm(search_audio_files()))


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
    for (id, name) in files:
        try:
            _play_one(id, name)
        except Exception as e:
            logger.warning("Failed to play '%s' (%s) due to the error:\n%s", name, id, e)


def _play_one(id, name):
    # download file
    service = create_api_service()
    request = service.files().get_media(fileId=id)
    with TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, name)
        with open(filepath, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                logger.info("Download %d%%.", int(status.progress() * 100))
        
        #command = ["ffplay", filepath, "-autoexit", "-nodisp"]
        #p = subprocess.run(command, stdin=subprocess.PIPE)
        command = ["mplayer", "-vo", "null", filepath]
        p = subprocess.run(command)
    

def main():
    basicConfig(level=20, format="[%(levelname).1s|%(asctime).19s|%(name)s] %(message)s")
    parser = ArgumentParser(description="Play music files in google drive")
    parser.add_argument("-U", "--update-list", action="store_true", help="Update file list")
    parser.add_argument("-c", "--credential-json", type=str, default="_credentials.json",
                        help="Path to the google cloud credential JSON file with google drive permission")
    parser.add_argument("-d", "--database-file", type=str, default="_gdriveplayer.db",
                        help="Path to the sqlite database file")

    args = parser.parse_args()
    config.credentialjson = os.path.abspath(args.credential_json)
    config.dbfile = os.path.abspath(args.database_file)

    if args.update_list or not os.path.isfile(config.dbfile):
        update_audio_database(replace=True)

    play()

if __name__ == "__main__":
    main()
