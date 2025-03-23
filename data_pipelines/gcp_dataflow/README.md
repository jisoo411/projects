# Introduction: Dataflow Coding Examples

Within this subfolder are examples demonstrating code that utilizes Apache Beam and structured to run within Google Cloud Platform, in particular Google Pub/Sub as the message broker and Google Cloud Storage as the destination.

_**01_cleanse_and_filter.py**_
This Apache Beam script demonstrates the following:
1. Source: Google Pub/Sub subscription
2. Target: Google Cloud Storage bucket, Parquet file format.
3. Defining classes that extend Apache Beam's DoFN class to do the following:
    1. Basic data and json structure validation for messages.
    2. Conversion of message data into Parquet file format.
    3. Upload Parquet files to GCS bucket destination.
    4. Error handling for basic edge cases.

_NOTE_ This example handles messages individually and writes each to an individual Parquet file.  Not necessarily efficient for downstream consumption, the next example introduces processing and grouping messages in fixed windows.

_**02_cleanse_and_filter_grouped.py**_
_Coming soon._