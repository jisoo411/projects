# This Dataflow script reads raw messages from the PubSub subscription 
# then checks the format for valid json structure.  The messages are
# then written to GCS in Parquet format for further processing.

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.io.gcp.pubsub import ReadFromPubSub, DeadLetterPolicy
import pyarrow.parquet as pq
import pyarrow as pa
import json
import tempfile
import os
from google.cloud import storage

class ConvertToParquet(beam.DoFn):
    ## This class extends the DoFn class and overrides the process method to convert the PubSub message to a Parquet file.
    ## It performs the following steps:
    ## - Read a message from PubSub, check for valid JSON format.
    ##      - If the message is empty or not in JSON format, disregard it.
    ##      - If the message is valid JSON, store it locally.
    ## - Convert the raw message to JSON format and store it in a PyArrow table.
    ## - Write the table data to a temporary Parquet file and yield the file path for the next transform.
    def process(self, element):
        try:
            # Element is a byte string, convert it to a Python string first
            message_str = element.decode('utf-8')
            print("Raw message string: {}".format(message_str))

            # Check if the message is empty or all white space, if so, disregard it
            if not message_str.strip():
                print("Empty message, disregard.")
                return

            # Assuming element is in proper JSON format, parse it and store it locally
            record = json.loads(message_str)
            print("Valid message contents: {}".format(record))

            # Record is a Python list of dictionary elements, convert them to a PyArrow table
            table = pa.Table.from_pylist([record])

            # Write the table to a parquet file in memory and yield the file's local file path for the next transform
            with tempfile.NamedTemporaryFile(delete=False, suffix=".parquet") as temp_file:
                pq.write_table(table, temp_file.name)
                print(f"Parquet file written to: {temp_file.name}")
                yield temp_file.name
                
        except json.JSONDecodeError as e:
            # Invalid json message caught, disregard and move on
            print(f"Invalid JSON message: {message_str}")
            return
        except Exception as e:
            # Any other unexpected errors are caught here 
            print(f"Unknown error: {e}")
            return

class UploadToGCS(beam.DoFn):
    ## This class extends the Beam DoFn class and overrides the process method to use the GCP GCS client 
    ## to upload the temp Parquet file to the GCS destination bucket.
    def __init__(self, bucket_uri):
        self.bucket_uri = bucket_uri

    def process(self, file_path):
        ## Upload the file to GCS and yield the public URL of the uploaded file
        try:
            # Create a GCS client and get a reference to the bucket
            print("Creating storage client object...")
            client = storage.Client()
            print(f"Creating bucket object for {self.bucket_uri}...")
            bucket = client.get_bucket(self.bucket_uri.replace("gs://", ""))
            print(f"Bucket object created: {bucket.path}")
            # Create a blob object that references the local temp file
            print(f"Creating blob object for {file_path}...")
            blob = bucket.blob(f"data/{os.path.basename(file_path)}")
            print(f"Blob object created...{blob.name}")
            # Upload the file to GCS via blob object reference
            print(f"Uploading file to GCS...")
            blob.upload_from_filename(file_path)
            print(f"File uploaded to GCS: {blob.public_url}")
            # Clean up the local temp file
            print(f"Removing local temp file: {file_path}")
            os.remove(file_path)
            # Return the public URI of the uploaded file
            yield blob.public_url
        except Exception as e:
            print(f"Error uploading file to GCS: {e}")
            return



def run():
    ## Declare GCP service variables, define pipeline options, and run the pipeline
    PROJECT_ID = "my-test-project-370503"
    PUBSUB_SUBSCRIPTION = f"projects/{PROJECT_ID}/subscriptions/gcp-dataflow-topic-001-sub"
    BUCKET_NAME = "gs://gp-ingest-source-001"
    SERVICE_ACCOUNT = "gp-df-service-account-001@my-test-project-370503.iam.gserviceaccount.com"
    DLQ_TOPIC = f"projects/{PROJECT_ID}/topics/gcp-dataflow-topic-001-dlq"

    options = PipelineOptions(
        streaming=True, # Set to True for streaming data
        project=PROJECT_ID,
        job_name="cleanse-and-filter",
        temp_location="{}/dataflow_temp".format(BUCKET_NAME),
        region="us-central1",
        runner="DirectRunner", # Run the pipeline locally for testing
        # runner="DataflowRunner", # Run the pipeline on Dataflow
        service_account_email=SERVICE_ACCOUNT
    )

    with beam.Pipeline(options=options) as p:
        (
            p
            | "Read messages from Pubsub" >> ReadFromPubSub(subscription=PUBSUB_SUBSCRIPTION)
            | "Convert messages to Parquet" >> beam.ParDo(ConvertToParquet())
            | "Upload Parquet files to GCS" >> beam.ParDo(UploadToGCS(BUCKET_NAME))
        )

if __name__ == "__main__":
    run()