aws codebuild start-build \
  --project-name docuedge-linking \
  --region us-west-2 \
  --environment-variables-override \
    name=ACTION,value=LOAD_LINKING,type=PLAINTEXT \
    name=API_DNS,value=api.example.com,type=PLAINTEXT \
    name=S3_BUCKET,value=my-bucket,type=PLAINTEXT \
    name=FILE_KEY,value=docuedge-migration/input/files.txt,type=PLAINTEXT \
    name=API_SECRET_ID,value=docuedge/api,type=PLAINTEXT \
    name=HTTP_TIMEOUT,value=60,type=PLAINTEXT
