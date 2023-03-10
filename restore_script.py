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
	oauth_token = get_oauth_token(oauth_endpoint, api_key).json()["access_token"]
	objects = list_objects(oauth_token, cos_endpoint, bucket_name)
	selected_objects = select_objects(objects, date, tier)
	restore_resquest_data, restore_request_headers = assemble_restore_request(oauth_token, tier, days)
	restore_objects(restore_resquest_data, restore_request_headers, selected_objects)
	return 0
	
def get_oauth_token(oauth_endpoint, api_key):
	logging.info("Retreiving oauth token...")
	headers = {"Accept": "application/json",
		       "Content_Type": "application/x-www-form-urlencoded"}
	data    = {"apikey": api_key,
		       "response_type": "cloud_iam",
		       "grant_type": "urn:ibm:params:oauth:grant-type:apikey"}
	r = requests.post(oauth_endpoint, headers=headers, data=data)
	logging.info("Oauth token retrieved")
	return r

def list_objects(oauth_token, cos_endpoint, bucket_name, params={"list-type": 2}):
	url = f"https://{cos_endpoint}/{bucket_name}"
	headers = {"Authorization": f"bearer {oauth_token}"}
	objects = []
	continuation_token = ""
	is_truncated = "true"

	while is_truncated == "true":
		if continuation_token:
			params = {"list-type": 2, "continuation-token": continuation_token}

		logging.info("Listing objects...")
		root = ET.fromstring(requests.get(url=url, params=params, headers=headers).text)

		for element in root.iter():
			if element.tag == "{http://s3.amazonaws.com/doc/2006-03-01/}NextContinuationToken":
				continuation_token = element.text
				logging.info("Found continuation token: %s", continuation_token)
			elif element.tag == "{http://s3.amazonaws.com/doc/2006-03-01/}IsTruncated":
				is_truncated = element.text
				
		for element in root.iter("{http://s3.amazonaws.com/doc/2006-03-01/}Contents"):
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

	logging.info("Object list received. %d objects found", len(objects))
	return objects

def select_objects(object_collection, date, tier):
	logging.info("Selecting objects...")
	selected_objects = []
	
	for object in object_collection:
		if object["scl"] == tier and object["lmd"] > date:
			selected_objects.append(object)
			logging.info("Object %s - storage class %s - last modified date %s selected", object["key"], object["scl"], object["lmd"])

	logging.info("Finished selecting objects. %d objects were selected", len(selected_objects))
	return selected_objects

def assemble_restore_request(oauth_token, tier, days):
	logging.info("Assembling object restoration request data and headers...")
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
	logging.info("Initiating object restore...")

	for object in selected_objects:
		object_name = urllib.parse.quote(object.get("key"))
		logging.info("Restoring object %s", object_name)
		response = requests.post(f"https://{cos_endpoint}/{bucket_name}/{object_name}?restore", data=data, headers=headers)
		logging.info("Restore request response: %s", response.content)
		
	logging.info("All objects restored")

if __name__ == "__main__":
	main()