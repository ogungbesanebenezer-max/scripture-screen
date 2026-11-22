# Scripture Screen

Free software for churches. It listens to the preacher, finds the Bible verse being read or quoted, and puts it on
the projector or TV by itself (AUTO) or when the operator presses Space. It also shows hymns, posters, videos and the
church's own logo. Runs on an ordinary Windows laptop with no graphics card and **no internet during service**.

Made for Nigerian churches first: it is tuned for Nigerian English accents and shows Yoruba under English.

## Install (Windows, one time)

1. Download **ScriptureScreen-Setup.exe** from the [Releases page](../../releases) (about 870 MB).
2. Double-click it. If Windows says "Windows protected your PC", click **More info**, then **Run anyway**.
3. Click Next until it finishes (about 5 minutes). No internet and no admin password are needed.
4. Open **Scripture Screen** from the desktop icon.

Then, once: click **OUR CHURCH** to add your church's name, logo and colour, and **FIND HYMN** to add your hymns.

Needs: Windows 10 or 11 (64-bit), about 2 GB of free disk space, 8 GB of memory recommended.

### From source (developers, Mac or Linux)

Download this repo. Windows: double-click `setup.bat`. Mac/Linux: install Python 3.12, then run `./setup.sh`
(on Linux first `sudo apt install python3-tk libportaudio2`). Setup needs internet once (about 1 GB of models).
To build the Windows installer yourself: `python3 installer/build_installer.py --windows-app <a Windows copy>`
with [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed.

## Run

Windows: double-click `start.bat`. Mac/Linux: `./start.sh`. On Windows the app opens with no black window, so it
cannot be closed by accident; if it will not open, `start_with_messages.bat` shows why.

Before each service press **READY FOR SERVICE? (F5)**. It lists, in plain words, whether the speech engine, the
sound input, the TV, the Bible and the charger are in order, and can put a test verse on the TV.

Connect the TV or projector and set the display to **Extend** (Windows: `Win + P`). The app finds the external
screen itself. A splitter feeding TV and projector together needs no change.

For sound, run a cable from the mixer's Aux or Rec output to the laptop's mic input or a USB audio adapter, then
pick it under **Sound input**. The green bar should move when someone speaks.

| Key | Action |
|---|---|
| Space | Push the preview to the screen |
| Esc | Clear the screen |
| Left / Right | Previous / next verse |
| Ctrl+Z / Ctrl+Y | Undo / redo what is on screen |
| F2 | Wrong verse: take it off and type the right one |
| F5 | Ready-for-service check |

## Two modes

**AUTO (ticked, no operator needed).** A verse goes on screen by itself when its confidence score reaches 80 of 100:
book or passage in play +50, command words ("media give me", "go to") +30, "chapter/verse/vs" +20, a sentence that is
only "Verse 2" +30, and -40 for number talk like "18 years old", "8 o'clock" or "room 8". It fades in whole, never split into pages: long verses shrink
to fit. A verse stays up until the next verse, however long the pastor
stays on it. "Amen", "Praise the Lord", "Let us pray" or "Hallelujah" fade it to the standby screen.

The screen is never blank: when no verse is up it shows the church logo set in **OUR CHURCH** (or "Welcome"
before a logo is added). OUR CHURCH also sets the colour of verse titles and a small logo in a corner of each verse.

**Operator (AUTO unticked).** Nothing reaches the screen until the operator presses Space. A spoken version change
still updates a verse already on screen.

Lower-scoring verses and all paraphrases only appear in the Detected list. If no version is named, the
**Default Bible** (KJV unless changed) is used. To change the closing phrases, add `"clear_phrases": ["let us pray"]` to `settings.json`.

## During a service

- Unplugged sound cable or USB adapter: the app reconnects by itself. If that input is gone, it uses another one.
- HDMI plugged in late or pulled out: the projector window moves by itself within about 5 seconds.
- The laptop will not sleep by itself while the app is open (Windows). Closing the lid still sends it to sleep, so
  keep the lid open; if it did sleep, the app says so and carries on.
- Closing the verse window does nothing, and a damaged `settings.json` or a full disk does not stop the app starting.
- Problems, and each sentence heard (so a wrong verse can be traced), are recorded in `logs/app.log` on the laptop
  only.

If the pastor changes their mind ("John 3:16, no wait, let's make it Genesis 1:1"), the verse they took back is dropped
and the correction goes up. "Go back to the first one" returns to a verse shown earlier.

The status line shows **hearing** as a percentage. Phrases under 50% are ignored and the screen stays as it is. To
change that, add `"min_confidence": 40` to `settings.json`.

## What it understands

- References: "John chapter three verse sixteen", "First Corinthians 13 verses 4 to 7", "Psalm 23".
- Direct commands: "Media, give me Genesis 1 vs 1", "Media, project Jude 1 vs 7". These load at once and are marked
  **Pastor asked**.
- Moving within a passage: "go to verse 8", "verse 2", "let's go to 18", "go to chapter 5", "next verse".
- Versions: "let's look at that in the Amplified version", "give me King James".
- Paraphrases: "God loved the world so much he gave his son". These are marked **Paraphrase?** because they are
  best guesses. The operator should glance at them before going live.

## Two languages on screen

Pick a version under **Also show**, such as YOR, and every verse appears in the Bible being read with that version
underneath. Pages turn together. A verse missing from the second version shows alone. Choose `None` to turn it off.

## Tune it for your pastor's accent

Watch **Hearing now** during service. When a book name comes out wrong, add it to `accent_corrections.json`:

```json
"happen cock": "Habakkuk"
```

Restart the app. Corrections only apply when a number follows, so they cannot damage normal speech.

Built in already: "thirteen" heard as "thirty" (and sixteen/sixty and the like) is fixed when the number heard does
not exist in that chapter, and "Media, project you chapter 1 verse 7" reads as Jude. To measure the app on Nigerian
English voices: `pip install edge-tts`, then `python scripts/accent_bench.py make` and
`python scripts/accent_bench.py run base.en small.en`.

## Add NIV, NKJV, AMP or other licensed Bibles

Included: KJV, ASV, BBE, YLT, and two modern-English Bibles, BSB (Berean Standard Bible) and WEB (World English
Bible), all public domain; and YOR, the Open Yoruba Contemporary Bible (CC BY-SA 4.0, see
`data/bibles/NOTICE.txt`). Say "in Yoruba" to switch to it (to the Bibeli Mimo, `BM`, when that file is in
`my_bibles/`). The app listens in English, so the reference itself must
be spoken in English. NIV, NKJV, AMP, ESV and similar are copyrighted, so get
permission or a licence first. Then put the file in `my_bibles/` (format in `my_bibles/README.txt`) and start the app:
it notices the new file and updates itself. That folder is never uploaded with the repo. A verse that a Bible leaves
out is shown from the KJV rather than leaving the screen empty.


## Hymns

**FIND HYMN** searches 270 free (public-domain) hymns that come with the app, and with internet also Wikisource, the
free library of public-domain texts. **IMPORT** reads your own hymn book: a Word or PDF file with numbered hymns
("23", "Hymn 23", "Orin 23"), text files, or OpenLyrics files from other worship software. Modern worship songs are
copyrighted: show them under your church's own song licence (for example CCLI).

## Rehearse without a microphone

`start.bat --simulate sermon.wav` or `./start.sh --simulate sermon.wav` plays a recording as if it were live.

## Developers

`pip install pytest && python -m pytest` (the window tests need a screen; on a server use `xvfb-run`). After any
change to verse detection also run `python scripts/replay_log.py logs/app.log` and compare with the run before.
Data is built by `verse_screen/database.py`; `scripts/` holds the
one-off tools that produced `data/`.

## Licence

Scripture Screen is free software under the GNU General Public License v3 (see `LICENSE`): anyone may use, share and
change it, and copies they share must stay free under the same licence. Bible texts keep their own licences
(`data/bibles/NOTICE.txt`); the hymns are public domain, from the Open Hymnal Project (openhymnal.org).
