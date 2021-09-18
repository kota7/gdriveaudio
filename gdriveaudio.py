# -*- coding: utf-8 -*-

import os
import re
import json
import csv
import sys
import sqlite3
import subprocess
import warnings
from argparse import ArgumentParser
from collections import namedtuple
#from logging import getLogger, basicConfig
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import chardet
from tqdm import tqdm
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.service_account import Credentials

#logger = getLogger(__name__)


class config:
    credentialjson = os.path.join(os.getcwd(), "_credentials.json")
    dbfile = os.path.join(os.getcwd(), "_gdriveplayer.db")
    encoding = "utf8"
    chardet_threshold = 0.95

# ***   DATABASE HELPERS ************************************************************ #
AudioFile = namedtuple("AudioFile", "id name mimetype parent size md5checksum")
AudioMeta = namedtuple("AudioMeta", "id title artist album album_artist date year genre duration")
Folder    = namedtuple("Folder",    "id name parent fullpath")

def _get_sql(query: str, header: bool=False):
    with sqlite3.connect(config.dbfile) as conn:
        c = conn.cursor()
        c.execute(query)
        if header:
            yield [a[0] for a in c.description] # column names
        for row in c:
            yield row

def _exec_sql(query: str, value=None, values=None):
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

def _validate_sql(query: str, value=None, values=None)-> tuple:
    try:
        _exec_sql(query, value=value, values=values)
        return True, None
    except Exception as e:
        return False, e
# ***   END OF DATABASE HELPERS ***************************************************** #


# ***   GOOGLE DRIVE HELPERS   ****************************************************** #
def _create_api_service():
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

def _fetch_file(id: str, name: str, tmpdir: str, service=None)-> str:
    if service is None:
        service = _create_api_service()
    request = service.files().get_media(fileId=id)
    filepath = os.path.join(tmpdir, name)
    with open(filepath, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            #logger.info("Download %d%%.", int(status.progress() * 100))
    return filepath

def search_audio_files():
    service = _create_api_service()

    page_token = None
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

def search_folders()-> list:
    service = _create_api_service()

    page_token = None
    folders = []
    while True:
        response = service.files().list(
            q="mimeType = 'application/vnd.google-apps.folder'",
            spaces='drive',
            pageSize=1000,
            fields='nextPageToken, files(id, name, parents)',
            pageToken=page_token
        ).execute()
        folders += response.get('files', [])
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break

    # check that parents is a list of length 1 and extract the element
    for f in folders:
        p = f.get("parents", [])
        assert len(p) <= 1
        parent = None if len(p)==0 else p[0]
        if "parents" in f: del f["parents"] 
        f["parent"] = parent
    folders = _add_fullpath(folders)
    folders = [Folder(**f) for f in folders]
    return folders

def _add_fullpath(folders: list, sep="/"):
    # folders is a list of dict(id, name, parent)
    
    # convert to a dict with id as the key
    # and initialize fullpath field as None
    # i.e. id --> dict(id, name, parent, fullpath)
    # this way, finding an entry by id is faster
    out = {}
    for item in folders:
        tmp = item.copy()
        tmp["fullpath"] = None
        out[item["id"]] = tmp

    # recursive function to update fullpath of a specific id
    def _fullpath(id):
        # avoid computing if this id has already been calculated
        if out[id]["fullpath"] is not None:
            return out[id]["fullpath"]
        if out[id]["parent"] not in out:
            # this id has no parent in the list
            # so this is a top folder, i.e. fullpath = name
            out[id]["fullpath"] = out[id]["name"]
            return out[id]["fullpath"]
        out[id]["fullpath"] = _fullpath(out[id]["parent"]) + sep + out[id]["name"]
        return out[id]["fullpath"]

    # make sure we calculate fullpath for all ids
    for id in out: _fullpath(id)
    # convert back to a list of dict
    return [v for _, v in out.items()]
# ***   END OF GOOGLE DRIVE HELPERS   ************************************************ #


# ***   PLAYER HELPERS   ************************************************************* #
def _validate_integer(x: str)-> int:
    if x is None:
        return x
    if re.match(r"\d+$", x) is None:
        print("'%s' does not seem integer --> None" % x)
        return None
    else:
        return int(x)

def _validate_numeric(x: str)-> float:
    if x is None:
        return x
    if re.match(r"\d+[\.]{0,1}\d*$", x) is None:
        print("'%s' does not seem numeric --> None" % x)
        return None
    else:
        return float(x)

def _guess_encoding(x: bytes)-> str:
    tmp = chardet.detect(x)
    enc = tmp.get("encoding", config.encoding)
    conf = tmp.get("confidence", 0)
    # if ascii is guessed, then we use the default encoding just in case
    if enc == "ascii":
        enc = config.encoding
    # if confidence is not high enough, we use the default encoding
    if conf < config.chardet_threshold:
        enc = config.encoding
    return enc

def _get_audiometa(filepath: str)-> dict:
    command = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath]
    p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    x = p.stdout
    enc = _guess_encoding(x)
    x = x.decode(enc, errors="ignore")
    x = json.loads(x)
    x = x.get("format", {})
    tags = x.get("tags", {})
    x = {key.lower().strip():val for key,val in x.items()}
    tags = {key.lower().strip():val for key,val in tags.items()}
    
    out = {
       "title": tags.get("title")
      ,"artist": tags.get("artist")
      ,"album": tags.get("album")
      ,"album_artist": tags.get("album_artist")
      ,"date": tags.get("date")
      ,"year": tags.get("year")
      ,"genre": tags.get("genre")
      ,"duration": x.get("duration")
    }
    if out["year"] is None and out["date"] is not None:
        r = re.match(r"\d{4}", out["date"])
        if r is not None:
            out["year"] = r.group(0)
    # numeric value validation
    out["year"] = _validate_integer(out["year"])
    out["duration"] = _validate_numeric(out["duration"])
    return out

def _play_audiofile(filepath: str):
    command = ["mplayer", "-vo", "null", filepath]
    p = subprocess.run(command)

def _generate_audiometa_data(ids: list, names: list):
    def _task(id, name):
        with TemporaryDirectory() as tmpdir:
            try:
                filepath = _fetch_file(id, name, tmpdir)
            except Exception as e:
                warnings.warn("Failed to fetch file '%s' '%s' due to error '%s'" % (id, name, e))
                return None
            meta = _get_audiometa(filepath)
            meta = AudioMeta(id=id, **meta)
            os.unlink(filepath)
            print(meta)
            return meta

    with ThreadPoolExecutor() as t:
        for meta in t.map(_task, ids, names):
            if meta is None: continue
            yield meta
# ***   END OF PLAYER HELPERS   ******************************************************* #


# ***   MAIN PROCEDURE   ************************************************************** #
def init_database():
    if os.path.isfile(config.dbfile):
        os.unlink(config.dbfile)

    _exec_sql("""
    CREATE TABLE IF NOT EXISTS audiofiles (
         id           TEXT UNIQUE PRIMARY KEY
        ,name         TEXT
        ,mimetype     TEXT
        ,parent       TEXT
        ,size         INTEGER
        ,md5checksum  TEXT
    )
    """)

    _exec_sql("""
    CREATE TABLE IF NOT EXISTS audiometa (
         id            TEXT UNIQUE PRIMARY KEY
        ,title         TEXT
        ,artist        TEXT
        ,album         TEXT
        ,album_artist  TEXT
        ,date          TEXT
        ,year          INTETER
        ,genre         TEXT
        ,duration      REAL
    )
    """)

    _exec_sql("""
    CREATE TABLE IF NOT EXISTS folders (
         id          TEXT UNIQUE PRIMARY KEY
        ,name        TEXT
        ,parent      TEXT
        ,fullpath    TEXT
    )
    """)

    _exec_sql("""
    CREATE VIEW IF NOT EXISTS audio AS
    SELECT
      a.*,
      f.name AS folder, f.fullpath || '/' AS prefix,
      m.title, m.artist, m.album_artist, m.date, m.year, m.duration
    FROM
      audiofiles AS a
      LEFT JOIN audiometa AS m ON a.id = m.id
      LEFT JOIN folders   AS f ON a.parent = f.id
    """)


def play_audio(filter: str=None, repeat: bool=False):
    q = "SELECT id, name, prefix FROM audio"
    if filter is not None:
        q += " WHERE {}".format(filter)
    q += " ORDER BY random()"
    flag, e = _validate_sql(q)
    if not flag:
        raise ValueError("Query is invalid:\n'{}'\nError:\n'{}'".format(q, e))
    while True:
        files = list(_get_sql(q))
        print("Found %d files" % len(files))
        for i, (id, name, prefix) in enumerate(files):
            with TemporaryDirectory() as tmpdir:
                try:
                    service = _create_api_service()
                    request = service.files().get_media(fileId=id)
                    filepath = _fetch_file(id, name, tmpdir)
                    print("***********************************************************")
                    print("Playing %d/%d: %s (at %s)" % (i+1, len(files), name, prefix))
                    _play_audiofile(filepath)
                except Exception as e:
                    warnings.warn("Failed to play '%s' (%s) due to the error:\n%s" % (name, id, e))
        if not repeat:
            print("Finished playing all files")
            break

def show_data(n: int=None, filter: str=None):
    q = "SELECT * FROM audio"
    if filter is not None:
        q += " WHERE {}".format(filter)
    if n is not None:
        q += " LIMIT {}".format(n)
    flag, e = _validate_sql(q)
    if not flag:
        raise ValueError("Query is invalid:\n'{}'\nError:\n'{}'".format(q, e))
    rows = _get_sql(q, header=True)
    writer = csv.writer(sys.stdout)
    for row in rows:
        writer.writerow(row)

def update_audio_data(files: bool=False, meta: bool=False, replace_meta: bool=False, folders: bool=False):
    if not os.path.isfile(config.dbfile):
        print("Initializing database")
        init_database()
    if files:
        print("Updating audio file list")
        _update_audiofiles()
    if meta:
        print("Updating audio meta data")
        _update_audiometa(replace=replace_meta)
    if folders:
        print("Updating the folder structure")
        _update_folders()

def _update_audiofiles():
    _exec_sql("DELETE FROM audiofiles")
    placeholder = ",".join("?" * len(AudioFile._fields))
    q = "INSERT INTO audiofiles VALUES ({})".format(placeholder)
    _exec_sql(q, values=tqdm(search_audio_files()))

def _update_audiometa(replace: bool = False):
    if replace:
        q = "SELECT id, name FROM audiofiles"
    else:
        q = "SELECT id, name FROM audiofiles WHERE id NOT IN (SELECT id FROM audiometa)"    
    files = [(row[0], row[1]) for row in _get_sql(q)]
    ids, names = zip(*files)
    total = len(ids)
    placeholder = ",".join("?" * len(AudioMeta._fields))
    q = "INSERT OR REPLACE INTO audiometa VALUES ({})".format(placeholder)
    _exec_sql(q, values=tqdm(_generate_audiometa_data(ids, names), total=total))


def _update_audiometa_one(id: str, filepath: str):
    meta = _get_audiometa(filepath)
    meta = AudioMeta(id=id, **meta)
    print("Audio metadata: %s", meta)
    placeholder = ",".join("?" * len(meta))
    q = "INSERT OR REPLACE INTO audiometa VALUES ({})".format(placeholder)
    _exec_sql(q, value=meta)


def _update_folders():
    _exec_sql("DELETE FROM folders")
    placeholder = ",".join("?" * len(Folder._fields))
    q = "INSERT INTO folders VALUES ({})".format(placeholder)
    _exec_sql(q, values=tqdm(search_folders()))


def main():
    parser = ArgumentParser(description="Play music files in google drive")
    subparsers = parser.add_subparsers(dest="command")
    
    parent_parser = ArgumentParser(add_help=False)
    parent_parser.add_argument("-c", "--credential-json", type=str, default="_credentials.json",
                        help="Path to the google cloud credential JSON file with google drive permission")
    parent_parser.add_argument("-d", "--database-file", type=str, default="_gdriveplayer.db",
                        help="Path to the sqlite database file")
    
    init = subparsers.add_parser("init", parents=[parent_parser],
                                 help="Initialize database (all existing data are delted)")
     
    update = subparsers.add_parser("update", help="Update data", parents=[parent_parser])
    update.add_argument("-U", "--update-filelist", action="store_true", help="Update file list")
    update.add_argument("-M", "--update-meta", action="store_true", help="Update audio metadata")
    update.add_argument("-F", "--update-folders", action="store_true", help="Update folder structure data")
    update.add_argument("--replace-meta", action="store_true", help="Replace existing metadata")

    play = subparsers.add_parser("play", help="Play audio", parents=[parent_parser])
    play.add_argument("-q", "--filter-query", type=str, default=None, help="SQL query to select files to play")
    play.add_argument("--repeat", action="store_true", help="Repeat forever")

    data = subparsers.add_parser("data", help="Show data in csv format", parents=[parent_parser])
    data.add_argument("-n", type=int, default=None, help="Number of rows to show")
    data.add_argument("-q", "--filter-query", type=str, default=None, help="SQL query to select files to show")

    args = parser.parse_args()
    #print(args)
    config.credentialjson = os.path.abspath(args.credential_json)
    config.dbfile = os.path.abspath(args.database_file)

    if args.command == "init":
        init_database()
    elif args.command == "update":
        update_audio_data(files=args.update_filelist, meta=args.update_meta,
                        replace_meta=args.replace_meta, folders=args.update_folders)
    elif args.command == "play":
        play_audio(filter=args.filter_query, repeat=args.repeat)
    elif args.command == "data":
        show_data(n=args.n, filter=args.filter_query)
# ***   END OF MAIN PROCEDURE   ******************************************************* #


if __name__ == "__main__":
    main()
