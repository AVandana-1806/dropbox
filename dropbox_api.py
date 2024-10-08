"""
This script downloads PDF files from the Dropbox Sign API.

Usage:
    python script_name.py <api_key>

Arguments:
    api_key: Dropbox Sign API key
"""

import argparse
import os
import logging
from dropbox_sign import ApiClient, ApiException, Configuration, apis


NEW_FOLDER_NAME = "dropbox"
PAGE_SIZE = 20
FAILED_DOWNLOADS_FILE = "failed_downloads.txt"


def main(api_key):
    """
    Main function to set up the API client and initiate PDF download.
    """
    configuration = Configuration(username=api_key)
    with ApiClient(configuration) as api_client:
        signature_request_api = apis.SignatureRequestApi(api_client)
        try:
            download_pdf_files(signature_request_api)
        except ApiException as e:
            logging.error("Exception when calling Dropbox Sign API: %s", e)


def download_pdf_files(signature_request_api):
    """
    Download PDF files from the Dropbox Sign API.
    """
    os.makedirs(NEW_FOLDER_NAME, exist_ok=True)
    page = 1

    if os.path.exists(FAILED_DOWNLOADS_FILE):
        os.remove(FAILED_DOWNLOADS_FILE)

    while True:
        try:
            signature_request_list = signature_request_api.signature_request_list(
                page=page, page_size=PAGE_SIZE
            )
        except ApiException as e:
            logging.error("Exception when retrieving signature requests: %s", e)
            break

        total_results = signature_request_list.list_info.num_results
        logging.info("Total Documents: %d", total_results)

        for signature_request in signature_request_list.signature_requests:
            download_pdf(signature_request, signature_request_api, NEW_FOLDER_NAME)

        if page * PAGE_SIZE < total_results:
            page += 1
        else:
            break


def download_pdf(signature_request, signature_request_api, folder_name):
    """
    Download a single PDF file from a signature request.
    """
    signature_request_id = signature_request.signature_request_id
    short_signature_request_id = signature_request_id[-6:]

    sanitized_title = signature_request.title.replace("/", "_")

    try:
        pdf_file_response = signature_request_api.signature_request_files(
            signature_request_id, file_type="pdf"
        )
    except ApiException as e:
        logging.error(
            "Exception when downloading file for request %s: %s",
            signature_request_id,
            e,
        )
        log_failed_download(sanitized_title, signature_request_id, e)
        return

    file_path = os.path.join(
        folder_name, f"{sanitized_title}_{short_signature_request_id}.pdf"
    )
    logging.info("Downloading file: %s", file_path)

    try:
        with open(file_path, "wb") as f:
            f.write(pdf_file_response.read())
    except IOError as e:
        logging.error("Exception when writing file %s: %s", file_path, e)
        log_failed_download(sanitized_title, signature_request_id, e)


def log_failed_download(title, signature_request_id, error):
    """
    Log the details of a failed download to a text file.
    """
    with open(FAILED_DOWNLOADS_FILE, "a", encoding="utf-8") as f:
        f.write(
            f"Failed to download {title} (ID: {signature_request_id}) - Error: {error}\n"
        )
    logging.info("Logged failed download: %s (ID: %s)", title, signature_request_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download PDF files from Dropbox Sign API"
    )
    parser.add_argument("api_key", type=str, help="Dropbox Sign API key")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    main(args.api_key)
