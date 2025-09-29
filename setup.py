"""
Setup script for the Photo-Flow package.
"""

from setuptools import setup, find_packages

setup(
    name="photo-flow",
    version="0.1.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "Click>=8.1.8",
        "Pillow>=11.2.1",
        "piexif>=1.1.3",
        "defusedxml>=0.7.1"
    ],
    entry_points={
        "console_scripts": [
            "photoflow=photo_flow.cli:photoflow",
        ],
    },
    python_requires=">=3.9",
    author="Johannes Krumm",
    author_email="johannes.krumm@example.com",
    description="CLI tool for managing Fuji X-T4 camera photos/videos",
    keywords="photography, workflow, fuji, camera",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Multimedia :: Graphics",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
    ],
)