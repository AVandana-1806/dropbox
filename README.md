# Dropbox Sign API PDF Downloader

This Python script downloads PDF files from the Dropbox Sign API using the provided API key.

## Setup

### Requirements
- Python 3.x
- Required Python packages (`dropbox_sign`, `argparse`)

## Usage

### Command-line Arguments

* api_key: Dropbox Sign API key.

## Running the Script

1. Navigate to the directory containing the script.
2. Run the script with the API key as an argument
```bash
python dropbox_api.py <your_dropbox_sign_api_key>
```

## Output
PDF files will be downloaded and saved in a local folder named dropbox.

## Notes
The script paginates through the signature requests to download all PDF files associated with each request.
Error handling is included to manage API exceptions.