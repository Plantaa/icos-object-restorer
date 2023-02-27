import os
import requests
import urllib.parse
import xml.etree.ElementTree as ET
import logging
import hashlib
import base64
from dotenv import load_dotenv

load_dotenv("values.env")
api_key = str(os.getenv("API_KEY"))
cos_endpoint = str(os.getenv("COS_ENDPOINT"))
bucket_name = str(os.getenv("BUCKET_NAME"))
days = str(os.getenv("DAYS"))
date = str(os.getenv("DATE"))
tier = str(os.getenv("TIER"))
oauth_endpoint="https://iam.cloud.ibm.com/oidc/token"

logging.root.handlers = []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("./logs"),
        logging.StreamHandler()
    ]
)

def main():
	logging.info("Retreiving oauth token...")
	oauth_token = get_oauth_token(oauth_endpoint, api_key).json()["access_token"]
	logging.info("Oauth token retrieved")

	logging.info("Listing objects...")
	object_collection, continuation_token, is_truncated = list_objects(oauth_token, cos_endpoint, bucket_name)
	logging.info("Object list received. %d objects found", len(object_collection))

	while is_truncated == "true":
		logging.info("Object list is truncated. Begining new listing of objects...")
		params = {"list-type": 2, "continuation-token": continuation_token}
		object_buffer, continuation_token, is_truncated = list_objects(oauth_token, cos_endpoint, bucket_name, params=params)
		logging.info("New objects listed. %d objects found", len(object_buffer))
		object_collection += object_buffer
	logging.info("All objects listed. Total objects: %d", len(object_collection))

	logging.info("Selecting objects...")
	selected_objects = select_objects(object_collection, date, tier)
	logging.info("Finished selecting objects. %d objects were selected", len(selected_objects))

	logging.info("Assembling object restoration request data and headers...")
	restore_resquest_data, restore_request_headers = assemble_restore_request(oauth_token, tier, days)

	logging.info("Initiating object restore...")
	restore_objects(restore_resquest_data, restore_request_headers, selected_objects)
	logging.info("All objects restored")
	return 0
	
def get_oauth_token(oauth_endpoint, api_key):
	headers = {"Accept": "application/json",
		       "Content_Type": "application/x-www-form-urlencoded"}
	data    = {"apikey": api_key,
		       "response_type": "cloud_iam",
		       "grant_type": "urn:ibm:params:oauth:grant-type:apikey"}
	r = requests.post(oauth_endpoint, headers=headers, data=data)
	return r

def list_objects(oauth_token, cos_endpoint, bucket_name, params={"list-type": 2}):
	url = f"https://{cos_endpoint}/{bucket_name}"
	headers = {"Authorization": f"bearer {oauth_token}"}
	# The IBM COS API returns a XML string, which the next line of code parses and stores the XML root element.
	root = ET.fromstring(requests.get(url=url, params=params, headers=headers).text)
	objects = []
	for element in root.iter("{http://s3.amazonaws.com/doc/2006-03-01/}Contents"):
		# Structure an object's schema to store the relevant information for restoration.
		# This includes the object's key, last modified date (lmd), and storage class (scl)
		object = {"key": "", "lmd": "", "scl": ""}
		for content in element:
			if content.tag == "{http://s3.amazonaws.com/doc/2006-03-01/}Key":
				object["key"] = content.text
			elif content.tag == "{http://s3.amazonaws.com/doc/2006-03-01/}LastModified":
				object["lmd"] = content.text.split("T")[0]
			elif content.tag ==  "{http://s3.amazonaws.com/doc/2006-03-01/}StorageClass":
				object["scl"] = content.text
		objects.append(object)
		logging.info("Object %s has ben added to the object list", object["key"])
	# Checks if there's a continuation token present, and whether the object listing is truncated
	# This will happen if the specified bucket contains more than 1000 objects.
	continuation_token = ""
	for element in root.iter():
		if element.tag == "{http://s3.amazonaws.com/doc/2006-03-01/}NextContinuationToken":
			continuation_token = element.text
		if element.tag == "{http://s3.amazonaws.com/doc/2006-03-01/}IsTruncated":
			is_truncated = element.text

	return objects, continuation_token, is_truncated

def select_objects(object_collection, date, tier):
	selected_objects = []
	for object in object_collection:
		if object["scl"] == tier and object["lmd"] > date:
			selected_objects.append(object)
			logging.info("Object %s - storage class %s - last modified date %s selected", object["key"], object["scl"], object["lmd"])

	return selected_objects

def assemble_restore_request(oauth_token, tier, days):
	restore_request = ET.ElementTree(ET.Element("RestoreRequest")).getroot()
	ET.SubElement(restore_request, "Days").text = days
	job_xml_element = ET.SubElement(restore_request, "GlacierJobParameters")
	ET.SubElement(job_xml_element, "Tier").text = tier
	data = ET.tostring(restore_request)
	logging.info("Request data assembled.\n%s", data)

	md5_encoded_data = base64.b64encode(hashlib.md5(data).digest())
	logging.info("Request data encoded: %s", md5_encoded_data)
	headers = {"Authorization": f"bearer {oauth_token}",
			   "Content-Type": "text/plain",
			   "Content-MD5": md5_encoded_data}
	logging.info("Request headers assembled")

	return data, headers

def restore_objects(data, headers, selected_objects):
	for object in selected_objects:
		object_name = urllib.parse.quote(object["key"])
		logging.info("Restoring object %s", object_name)
		response = requests.post(f"https://{cos_endpoint}/{bucket_name}/{object_name}?restore", data=data, headers=headers)
		logging.info("Restore request response: %s", response.content)

if __name__ == "__main__":
	main()
