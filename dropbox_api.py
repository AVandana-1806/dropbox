import argparse
import os

from dropbox_sign import ApiClient, ApiException, Configuration, apis

# Parse command-line arguments
parser = argparse.ArgumentParser(description="Download PDF files from Dropbox Sign API")
parser.add_argument("api_key", type=str, help="Dropbox Sign API key")
args = parser.parse_args()

configuration = Configuration(username=args.api_key)

# Create a new folder in Dropbox to save the PDF files
NEW_FOLDER_NAME = "dropbox"
new_folder_path = f"{NEW_FOLDER_NAME}"

with ApiClient(configuration) as api_client:
    signature_request_api = apis.SignatureRequestApi(api_client)
    try:
        # Create a new folder locally
        os.makedirs(new_folder_path, exist_ok=True)

        PAGE = 1
        PAGE_SIZE = 20

        while True:
            response = signature_request_api.signature_request_list(
                page=PAGE, page_size=PAGE_SIZE
            )

            print(f"Total Documents: {response.list_info.num_results}")

            total_results = response.list_info.num_results

            for item in response.signature_requests:
                signature_request_id = item.signature_request_id
                short_signature_request_id = signature_request_id[-6:]

                file_response = signature_request_api.signature_request_files(
                    signature_request_id, file_type="pdf"
                )
                file_path = (
                    f"{new_folder_path}/{item.title}_{short_signature_request_id}.pdf"
                )

                print(f"Downloading file: {item.title}.pdf")
                with open(file_path, "wb") as f:
                    f.write(file_response.read())

            if (PAGE - 1) * PAGE_SIZE < total_results:
                PAGE += 1
            else:
                break
    except ApiException as e:
        print(f"Exception when calling Dropbox Sign API: {e}\n")
