import json
import time

import boto3

import config
import eum

_tr = boto3.client("transcribe")
_s3 = boto3.client("s3")


def transcribe(media_id, timeout=75):
    """WhatsApp voice note -> S3 -> Amazon Transcribe (auto language) -> text. '' on failure."""
    key = f"voice/{media_id}.ogg"
    eum.download_media(media_id, config.MEDIA_BUCKET, key)
    job = f"wa-{media_id[:40]}-{int(time.time())}"
    _tr.start_transcription_job(
        TranscriptionJobName=job,
        Media={"MediaFileUri": f"s3://{config.MEDIA_BUCKET}/{key}"},
        MediaFormat="ogg",
        IdentifyLanguage=True,
        LanguageOptions=config.VOICE_LANGS,
        OutputBucketName=config.MEDIA_BUCKET,
        OutputKey=f"voice-out/{job}.json",
    )
    end = time.time() + timeout
    while time.time() < end:
        status = _tr.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]["TranscriptionJobStatus"]
        if status == "COMPLETED":
            body = _s3.get_object(Bucket=config.MEDIA_BUCKET, Key=f"voice-out/{job}.json")["Body"].read()
            return json.loads(body)["results"]["transcripts"][0]["transcript"].strip()
        if status == "FAILED":
            return ""
        time.sleep(2)
    return ""
