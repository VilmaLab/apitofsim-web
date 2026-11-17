The datasets can be prepared and histograms precomputed into database form on Turso like so

 $ RCLONE_S3_ACCESS_KEY_ID=xxx \
   RCLONE_S3_SECRET_ACCESS_KEY=xxx \
   UV_PROJECT=/path/to/apitofsim \
   VCPKG_ROOT=/path/to/vcpkg
   ./ingest-turso.sh

Everything needed will be grabbed from Allas, the ingestion will happen, and then the results put back on Allas.

Reminder, the keys are found using https://docs.csc.fi/support/faq/how-to-get-Allas-s3-credentials/
