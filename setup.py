# -*- coding: utf-8 -*-

import os
from setuptools import setup
from gdriveaudio import __version__

readmefile = os.path.join(os.path.dirname(__file__), "README.md")
with open(readmefile) as f:
    readme = f.read()

setup(
    name='gdriveaudio',
    version=__version__,
    description='Play music files on Google Drive',
    author='Kota Mori', 
    author_email='kmori05@gmail.com',
    long_description=readme,
    long_description_content_type='text/markdown',
    url='https://github.com/kota7/gdriveaudio',
    
    packages=[],
    py_modules=['gdriveaudio'],
    install_requires=['chardet', 'tqdm', 'google-api-python-client', 'google-auth-httplib2', 'google-auth-oauthlib'],
    entry_points={'console_scripts': ['gdriveaudio=gdriveaudio:main']},
)
