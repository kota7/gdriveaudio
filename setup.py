# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

setup(
    name='gdriveaudio',
    version='0.0.2',
    description='Play music files on Google Drive',
    author='Kota Mori', 
    author_email='kmori05@gmail.com',
    url='https://github.com/kota7/gdriveaudio',
    
    packages=[],
    py_modules=['gdriveaudio'],
    install_requires=['tqdm', 'google-api-python-client', 'google-auth-httplib2', 'google-auth-oauthlib'],
    entry_points={'console_scripts': ['gdriveaudio=gdriveaudio:main']},
)
