from __future__ import unicode_literals

from mopidy_btaudio import __version__
from setuptools import find_packages, setup

setup(
    name='Mopidy-BtAudio',
    version=__version__,
    url='https://github.com/djeebus/mopidy-btaudio',
    license='Apache License, Version 2.0',
    author='Joe Lombrozo',
    author_email='joe@djeebus.net',
    description='Mopidy extension for playing music from a2dp sources',
    long_description=open('README.rst').read(),
    packages=find_packages(),
    zip_safe=True,
    install_requires=[
        'bt_manager >= 0.3.0',
        'Mopidy >= 2.0',
        'Pykka >= 1.2',
    ],
    entry_points={
        'mopidy.ext': [
            'btaudio = mopidy_btaudio:BtAudioExtension',
        ],
    },
    package_data={
        b'mopidy_btaudio': ['ext.conf'],
    },
    classifiers=[
        'Environment :: No Input/Output (Daemon)',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2',
        'Topic :: Multimedia :: Sound/Audio :: Players',
    ],
)
