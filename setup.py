#!/usr/bin/env python


from setuptools import setup


setup(
    name='ss_eventlet',
    version='0.1.0',
    author='StyleSeat',
    description='Extensions for the eventlet concurrent networking library',
    url='https://github.com/styleseat/ss_eventlet',
    packages=['ss_eventlet'],
    install_requires=[
        'eventlet>=0.19.0',
    ],
    platforms='Platform Independent',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Operating System :: OS Independent',
        'Topic :: Internet',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
    ],
)
