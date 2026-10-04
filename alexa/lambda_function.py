"""AWS Lambda for the Darwish Smart Power Alexa skill.

Alexa smart home skills must answer through AWS Lambda; this function only forwards each directive
to the Darwish Smart Power server, which signs the customer in and switches their strips.
Set the environment variable SERVER_URL if the server is not https://power.darwish-tech.com.
"""

import json
import os
import urllib.request

SERVER = os.environ.get("SERVER_URL", "https://power.darwish-tech.com").rstrip("/")


def lambda_handler(event, context):
    request = urllib.request.Request(
        SERVER + "/api/alexa",
        data=json.dumps(event).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "DarwishAlexaLambda/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=7) as response:
            return json.loads(response.read())
    except Exception as err:  # the server is down or unreachable: tell Alexa so
        header = (event.get("directive") or {}).get("header") or {}
        error_header = {"namespace": "Alexa", "name": "ErrorResponse", "payloadVersion": "3",
                        "messageId": getattr(context, "aws_request_id", "0")}
        if header.get("correlationToken"):
            error_header["correlationToken"] = header["correlationToken"]
        return {"event": {"header": error_header,
                          "payload": {"type": "BRIDGE_UNREACHABLE", "message": str(err)[:200]}}}
