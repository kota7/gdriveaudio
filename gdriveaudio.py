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

__version__ = "0.1.9"

# ***   CONFIGURATION   ********************************************************** #
class config:
    credentialjson: str = None
    dbfile: str = None
    encoding: str = "utf8"
    chardet_threshold: float = 0.95
    mplayer = "mplayer"
    ffprobe = "ffprobe"

def _set_default_config():
    # 1. Use GDRIVEAUDIO_DIRECTORY env variable as the project root
    # 2. If 1 is not available, use currenct working directory as the project root
    workdir = os.environ.get("GDRIVEAUDIO_DIRECTORY", os.getcwd())
    if os.path.isdir(workdir):
        # use this directory as the working directory for this tool
        config.credentialjson = os.path.join(workdir, "_credentials.json")
        config.dbfile         = os.path.join(workdir, "_gdriveaudio.db")
    config.encoding = "utf8"
    config.chardet_threshold = 0.95

_set_default_config()

def _set_config(**kwargs):
    for key, value in kwargs.items():
        if not hasattr(config, key):
            print("'%' is not a valid config name; skipped")
            continue
        setattr(config, key, value)
# ***   END OF CONFIGURATION   *************************************************** #


# ***   DATABASE HELPERS   ********************************************************** #
AudioFile = namedtuple("AudioFile", "id name mimetype parent size md5checksum")
AudioMeta = namedtuple("AudioMeta", "id title artist album album_artist date year genre duration")
Folder    = namedtuple("Folder",    "id name parent fullpath")

def _database_exists()-> bool:
    return os.path.isfile(config.dbfile)

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
# ***   END OF DATABASE HELPERS   *************************************************** #


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
            #os.unlink(filepath)
            #print(meta)
            return meta

    with ThreadPoolExecutor() as t:
        for meta in t.map(_task, ids, names):
            if meta is None: continue
            yield meta
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
    command = [config.ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", filepath]
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
    command = [config.mplayer, "-vo", "null", filepath]
    p = subprocess.run(command)

def _check_mplayer():
    return _check_command([config.mplayer, "--help"])

def _check_ffprobe():
    return _check_command([config.ffprobe, "-version"])

def _check_command(command: list)-> bool:
    #print(command)
    try:
        p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        #print(p)
    except Exception as e:
        raise ValueError("'%s' does not seem a valid command; '%s' failed with error:: %s" % (
            config.ffprobe, " ".join(command), e))
    return True
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
      m.title, m.artist, m.album, m.album_artist, m.genre, m.date, m.year, m.duration
    FROM
      audiofiles AS a
      LEFT JOIN audiometa AS m ON a.id = m.id
      LEFT JOIN folders   AS f ON a.parent = f.id
    """)

def _compile_keyword(keyword, case_sensitive=False):
    #q = """SELECT name FROM pragma_table_info('audio') WHERE type LIKE 'TEXT'"""
    #textcols = [row[0] for row in _get_sql(q)]
    #textcols = [t for t in textcols if t not in ("md5checksum",)]  # disallow search on these columns
    # note: pragrma_table_info does not specify types for computed colums
    textcols = ["id", "name", "mimetype", "parent", "folder", "prefix", "title", "artist", "album_artist", "date"]
    #print(textcols)
    # field-specifig search
    r = re.search(r"([^:]+):(.*)", keyword)
    if r is not None:
        #print(r.groups())
        field, word = r.group(1), r.group(2)
        if field not in textcols:
            warnings.warn("'%s' is not a valid field name thus ignored (must be one of %s)" % (field, textcols))
        else:
            if case_sensitive:
                return "{} GLOB '*{}*'".format(field, word)
            else:
                return "{} LIKE '%{}%'".format(field, word)
    # search over all fields
    if case_sensitive:
        return " OR ".join("{} GLOB '*{}*'".format(field, keyword) for field in textcols)
    else:
        return " OR ".join("{} LIKE '%{}%'".format(field, keyword) for field in textcols)

def _compile_filter(query: str=None, keywords :list=None, keywords_case_sensitive: list=None):
    filters = []
    if query is not None:
        filters.append(query)    
    if keywords is not None:
        for k in keywords:
            filters.append(_compile_keyword(k, False))
    if keywords_case_sensitive is not None:
        for k in keywords_case_sensitive:
            filters.append(_compile_keyword(k, True))
    return " AND ".join("(%s)" % f for f in filters) if len(filters) > 0 else None


def play_audio(filter: str=None, repeat: bool=False):
    _check_mplayer()
    if not _database_exists():
        print("Database '%s' file not found. Run 'gdriveaudio update -U' first" % config.dbfile)
        return

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

def show_data(n: int=None, columns: list=None, filter: str=None):
    if not _database_exists():
        print("Database '%s' file not found. Run 'gdriveaudio update -U' first" % config.dbfile)
        return
    q = """SELECT name FROM pragma_table_info('audio')"""
    audio_cols = set([row[0] for row in _get_sql(q, header=True)])
    if columns is None:
        q = "SELECT * FROM audio"
    else:
        for c in columns:
            assert c in audio_cols, "'%s' is not a valid column name, must be one of %s" % (c, audio_cols)
        q = "SELECT {} FROM audio".format(",".join('"%s"' % c for c in columns))
    #print(q)
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
    if not _database_exists():
        print("Initializing database")
        init_database()
    if files:
        print("Updating audio file list")
        _update_audiofiles()
    if folders:
        print("Updating the folder structure")
        _update_folders()
    if meta:
        print("Updating audio meta data")
        _update_audiometa(replace=replace_meta)

def _update_audiofiles():
    _exec_sql("DELETE FROM audiofiles")
    placeholder = ",".join("?" * len(AudioFile._fields))
    q = "INSERT INTO audiofiles VALUES ({})".format(placeholder)
    _exec_sql(q, values=tqdm(search_audio_files()))

def _update_audiometa(replace: bool = False):
    _check_ffprobe()
    if replace:
        q = "SELECT id, name FROM audiofiles"
    else:
        q = "SELECT id, name FROM audiofiles WHERE id NOT IN (SELECT id FROM audiometa)"    
    files = [(row[0], row[1]) for row in _get_sql(q)]
    if len(files)==0:
        print("No audio files found to update the meta data")
        return
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
    parent_parser.add_argument("-D", "--workdir", type=str, default=None,
                               help=("Directory name of the project files (_credentials.json and _gdriveaudio.db"
                                    ". Deafult: 'GDRIVEAUDIO_DIRECTORY' environment variable, if given"
                                    ".          Otherwise the current directory"))
    parent_parser.add_argument("-c", "--credential-json", type=str, default=None,
                               help="Override the path to the google cloud credential JSON file with google drive permission")
    parent_parser.add_argument("-d", "--database-file", type=str, default=None,
                               help="Override the path to the sqlite database file")

    init = subparsers.add_parser("init", parents=[parent_parser],
                                 help="Initialize database (all existing data will be deleted)")

    update = subparsers.add_parser("update", help="Update data", parents=[parent_parser])
    update.add_argument("-U", "--update-filelist", action="store_true", help="Update file list")
    update.add_argument("-M", "--update-meta", action="store_true", help="Update audio metadata")
    update.add_argument("-F", "--update-folders", action="store_true", help="Update folder structure data")
    update.add_argument("--replace-meta", action="store_true", help="Replace existing metadata")
    update.add_argument("--metadata-encoding", type=str, default="utf8", help="Default encoding for audio metadata")
    update.add_argument("--chardet-threshold", type=float, default=0.95, help="Threshold to trust the chardet result")
    update.add_argument("--ffprobe", type=str, default="ffprobe", help="ffprobe command name")

    search = ArgumentParser(add_help=False)
    search.add_argument("-k", "--keyword", type=str, nargs="*",
                        help="keyword(s) to search, case-insensitive (name:word form to search in a specific field)")
    search.add_argument("-K", "--keyword-case-sensitive", type=str, nargs="*",
                        help="keyword(s) to search, case-sensitive (name:word form to search in a specific field)")
    search.add_argument("-q", "--filter-query", type=str, default=None, help="SQL query to select files to show")

    play = subparsers.add_parser("play", help="Play audio", parents=[search, parent_parser])
    play.add_argument("--repeat", action="store_true", help="Repeat forever")
    play.add_argument("--mplayer", type=str, default="mplayer", help="mplayer command name")

    data = subparsers.add_parser("data", help="Show data in csv format", parents=[search, parent_parser])
    data.add_argument("-n", type=int, default=None, help="Number of rows to show")
    data.add_argument("--columns", type=str, nargs="+", help="Columns to show")

    args = parser.parse_args()
    #print(args)
    if args.command is None:
        # called without subcommand, show help and finish
        parser.print_help()
        return

    # Credentials and database file locations
    # 1. If '--credential-json' or '--database-file' is given, use these values
    # 2. If '--workdir' is given, then use '{workdir}/_credentials.json' and '{workdir}/_gdriveaudio.db'
    # 3. Use '_credentials.json' and '_gdriveaudio.db' in the currenct directory
    if args.workdir is not None:
        _set_config(credentialjson=os.path.join(args.workdir, "_credentials.json"),
                    dbfile=os.path.join(args.workdir, "_gdriveaudio.db"))
    if args.credential_json is not None:
        _set_config(credentialjson=args.credential_json)
    if args.database_file is not None:
        _set_config(dbfile=args.database_file)

    if args.command == "init":
        init_database()
    elif args.command == "update":
        _set_config(encoding=args.metadata_encoding, chardet_threshold=args.chardet_threshold, ffprobe=args.ffprobe)
        update_audio_data(files=args.update_filelist, meta=args.update_meta,
                          replace_meta=args.replace_meta, folders=args.update_folders)
    elif args.command == "play":
        _set_config(mplayer=args.mplayer)
        filter = _compile_filter(query=args.filter_query, keywords=args.keyword, keywords_case_sensitive=args.keyword_case_sensitive)
        play_audio(filter=filter, repeat=args.repeat)
    elif args.command == "data":
        filter = _compile_filter(query=args.filter_query, keywords=args.keyword, keywords_case_sensitive=args.keyword_case_sensitive)
        show_data(n=args.n, columns=args.columns, filter=filter)
# ***   END OF MAIN PROCEDURE   ******************************************************* #


if __name__ == "__main__":
    main()
