Put licensed translations here (NIV, NKJV, AMP, ESV...), one JSON file each, then run setup again.
Files in this folder are never uploaded to the repo.

Format:
{"code": "NIV", "name": "New International Version",
 "books": {"Genesis": {"1": {"1": "verse text", "2": "verse text"}}}}

Book names must match exactly: Psalms, Song of Solomon, 1 Samuel, Revelation.

NKJV.json and BM.json (Yoruba Bibeli Mimo) are downloaded here by setup, or by get_bibles.bat /
`python scripts/get_private_bibles.py`. Start the app afterwards: it adds new files by itself.
