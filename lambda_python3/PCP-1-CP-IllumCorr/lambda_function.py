import json
import os
import sys
import boto3
import ast

sys.path.append("/opt/pooled-cell-painting-lambda")

import create_CSVs
import run_DCP
import create_batch_jobs
import helpful_functions

print("Loading function")

s3 = boto3.client("s3")
metadata_file_name = "/tmp/metadata.json"
fleet_file_name = "illumFleet.json"
step = "1"


def lambda_handler(event, context):
    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    key = event["Records"][0]["s3"]["object"]["key"]
    prefix, batchAndPipe = key.split("pipelines/")
    image_prefix = prefix.split("workspace")[0]
    batch = batchAndPipe.split("1_")[0][:-1]

    # Get the metadata file
    metadata_on_bucket_name = os.path.join(prefix, "metadata", batch, "metadata.json")
    metadata = helpful_functions.download_and_read_metadata_file(
        s3, bucket, metadata_file_name, metadata_on_bucket_name
    )

    # Standard vs. SABER configs
    if "SABERdict" not in list(metadata.keys()):
        SABER = False
        print ("Not a SABER experiment")
    if "SABERdict" in list(metadata.keys()):
        if not metadata["SABERdict"]:
            SABER = False
            print ("Not a SABER experiment")
    if "SABERdict" in list(metadata.keys()):
        if metadata["SABERdict"]:
            SABER = True
            print ("SABER experiment")

    # Calculate number of images from rows and columns in metadata
    num_series = int(metadata["painting_rows"]) * int(metadata["painting_columns"])
    # Overwrite rows x columns number series if images per well set in metadata
    if "painting_imperwell" in list(metadata.keys()):
        if metadata["painting_imperwell"] != "":
            if int(metadata["painting_imperwell"]) != 0:
                num_series = int(metadata["painting_imperwell"])

    # Get the list of images in this experiment
    if not SABER:
        parse_name_filter="20X_CP_"
    if SABER:
        parse_name_filter=""
    image_list_prefix = image_prefix + batch + "/images/"
    image_list = helpful_functions.paginate_a_folder(s3, bucket, image_list_prefix)
    image_dict = helpful_functions.parse_image_names(
        image_list, filter_in=parse_name_filter, filter_out="copy"
    )
    metadata["painting_file_data"] = image_dict
    helpful_functions.write_metadata_file(
        s3, bucket, metadata, metadata_file_name, metadata_on_bucket_name
    )

    # How many files/well indicates the well has all images present
    if metadata["one_or_many_files"] == "one":
        full_well_files = 1
    else:
        full_well_files = num_series

    # Pull the file names we care about, and make the CSV
    platelist = list(image_dict.keys())
    if not SABER:
        for eachplate in platelist:
            platedict = image_dict[eachplate]
            well_list = list(platedict.keys())
            paint_cycle_name = list(platedict[well_list[0]].keys())[0]
            # Only keep full wells
            per_well_im_list = []
            for eachwell in well_list:
                per_well = platedict[eachwell][paint_cycle_name]
                per_well.sort()
                if len(per_well) == full_well_files:
                    per_well_im_list.append(per_well)
            bucket_folder = (
                "/home/ubuntu/bucket/"
                + image_prefix
                + batch
                + "/images/"
                + eachplate
                + "/"
                + paint_cycle_name
            )
            per_plate_csv = create_CSVs.create_CSV_pipeline1(
                eachplate,
                num_series,
                bucket_folder,
                per_well_im_list,
                metadata["one_or_many_files"],
            )
            csv_on_bucket_name = (
                prefix
                + "load_data_csv/"
                + batch
                + "/"
                + eachplate
                + "/load_data_pipeline1.csv"
            )
            with open(per_plate_csv, "rb") as a:
                s3.put_object(Body=a, Bucket=bucket, Key=csv_on_bucket_name)
    if SABER:
        for eachplate in platelist:
            platedict = image_dict[eachplate]
            well_list = list(platedict.keys())
            SABERdict = ast.literal_eval(metadata["SABERdict"])
            SABERrounds = list(SABERdict.keys())
            # Only keep full wells
            print (f"{full_well_files} expect files per well and round for {eachplate}")
            incomplete_wells = []
            for eachwell in well_list:
                for eachround in SABERrounds:
                    per_well = platedict[eachwell][eachround]
                    if len(per_well) != full_well_files:
                        incomplete_wells.append(eachwell)
                        print (f"{eachwell} {eachround} doesn't have full well files. {len(per_well)} files found.")
            if incomplete_wells:
                for well in incomplete_wells:
                    del platedict[well]
            bucket_folder = (
                "/home/ubuntu/bucket/"
                + image_prefix
                + batch
                + "/images/"
                + eachplate
                + "/"
            )
            per_plate_csv = create_CSVs.create_CSV_pipeline1_SABER(
                eachplate,
                num_series,
                bucket_folder,
                platedict,
                metadata["one_or_many_files"],
                metadata["SABERdict"],
            )
            csv_on_bucket_name = (
                prefix
                + "load_data_csv/"
                + batch
                + "/"
                + eachplate
                + "/load_data_pipeline1.csv"
            )
            with open(per_plate_csv, "rb") as a:
                s3.put_object(Body=a, Bucket=bucket, Key=csv_on_bucket_name)

    # Now it's time to run DCP
    app_name = run_DCP.run_setup(bucket, prefix, batch, step)

    # Make a batch
    if not SABER:
        pipeline_name = "1_CP_Illum.cppipe"
    if SABER:
        pipeline_name = "1_SABER_CP_Illum.cppipe"
    create_batch_jobs.create_batch_jobs_1(
        image_prefix, batch, pipeline_name, platelist, app_name
    )

    # Start a cluster
    run_DCP.run_cluster(bucket, prefix, batch, step, fleet_file_name, len(platelist))

    # Run the monitor
    run_DCP.run_monitor(bucket, prefix, batch, step)
    print("Go run the monitor now")
