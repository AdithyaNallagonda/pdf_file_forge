# FileForge — how to use it

## Installing (once)

Double-click **FileForge-Setup.exe** and click Next until it finishes.
No password or administrator rights are needed. When it asks, tick both boxes —
the desktop shortcut and the right-click menu both save time later.

## Turning PDFs into files Claude can read

Pick whichever of these feels easiest. All three do the same thing.

**A. Drag and drop (best for many files at once)**
Select your PDFs in the folder where they live — you can select dozens — and drag
them onto the **FileForge** icon on the desktop. The app opens with them loaded.
Click **★ PDF → Markdown**. It works through them one at a time and tells you when
it's finished.

**B. Right-click (best for one file)**
Right-click a PDF, choose **Convert to Markdown (FileForge)**. It starts by itself.

**C. A whole folder at once (best for a case file)**
Open FileForge, click **Add folder…**, and pick the folder holding the case
documents. It finds every PDF inside it, including everything in sub-folders, and
tells you how many it found. Then click the action you want and leave it running —
it works through them several at a time. One unreadable document is skipped and
noted; the rest still finish.

**D. Open the app first**
Click **FileForge** in the Start Menu, then **Browse…** and pick your files.

When it's done, it asks if you'd like to open the results folder. Say yes — the
converted files are waiting there. They are also always at:

**D:\FileForge\result_files**

## Uploading to Claude

Drag the `.md` file from that folder straight into the Claude chat box, then ask
your question as normal.

**If a document was long**, FileForge will have made a *folder* instead of a single
file — for example `Award_2019_markdown`. Open it and you'll find
`Award_2019_part01.md`, `part02`, and so on. Upload them all in one go. Each part
is labelled at the top with which pages it covers, so Claude knows how they fit
together. There is also an `_INDEX.md` you can ignore or upload with the rest.

Splitting happens because a single very long file can be too much for one chat.
If you'd rather have fewer, larger parts, change **Split Markdown every (MB)** in
the app from 5 to a bigger number. Setting it to 0 turns splitting off entirely.

## Documents in Hindi, Telugu and other Indian languages

FileForge reads Indian languages as well as English. Before converting, click
**Choose…** next to *Document languages* and tick the languages that appear in
the papers — English, Hindi and Telugu are ticked to begin with.

**A document can contain several languages at once and that is fine.** If an
award is written in English with Hindi annexures, tick both — FileForge reads
both, even where they appear on the same page. Put the language the document is
mostly written in first if you can.

FileForge also checks the document itself and quietly drops any language it
cannot see. If you leave English, Hindi and Telugu ticked but a file turns out to
be entirely in English, it reads it as English only — faster, and more accurate.
So leaving all three ticked is safe for a mixed pile of papers. Untick
**Auto-narrow to scripts found** if you would rather it always used exactly what
you ticked.

Tick only what you actually need. Two or three languages is the sweet spot; every
extra one makes reading slower and can make the others slightly less accurate. If
a document is entirely in one language, tick just that one and English.

Some older Indian documents were typed in fonts that pre-date modern computing
(names like Kruti Dev, Shree Lipi, Anu or Godavari). These look like ordinary
text on screen but come out as nonsense when converted. FileForge spots them and
quietly re-reads those pages with text recognition instead. You will see a note
in the log when this happens.

If a converted file comes out as gibberish anyway — strings of meaningless Latin
letters where Hindi or Telugu should be — tick **Force OCR (ignore the text
layer)** and convert it again. That tells FileForge to disregard what is embedded
in the file and read the pages as pictures instead. It is slower, but it works
where the embedded text is unusable.

Always have someone check converted Indian-language text against the original
before relying on it. Recognition of Devanagari and Telugu script is good but
noticeably less reliable than for English, particularly on faint photocopies.

## Scanned documents

Old exhibits that were scanned as pictures have no text in them for a computer to
read. FileForge runs them through text recognition automatically — it's slower
(expect a minute or two per hundred pages) and the result is very good but not
perfect, so check names, dates and figures against the original before relying on
them in a filing.

If a converted file comes out saying *"no extractable content"* on every page, the
text recognition engine wasn't installed. Tell whoever set this up for you.

## Drawings, charts and diagrams

A converted file cannot contain the drawing itself, but FileForge does not simply
throw it away. Wherever it finds artwork it writes a short entry describing it —
what kind of drawing it is, how much of the page it covers, its caption, and every
label and figure it can read inside it. So a cross-section becomes a few lines
listing its levels, slopes and layer thicknesses, and Claude can tell you what is
on that page without you having opened it.

Each converted file also starts with a **Contents of this part** list showing which
pages hold drawings or tables. You never have to hunt through a document to find
out what is in it.

Where a diagram is made of boxes joined by lines — a process chart, an approval
chain — FileForge rebuilds the actual structure, and Claude can follow it. It only
does this when the shape is unambiguous; it will not guess. Treat any rebuilt
diagram as a reading aid and check it against the original before relying on it.

If you want the drawings themselves examined, tick **Also save figure pages as
images** before converting. Those pages are saved as pictures in a `figures`
folder, and you can drag any of them into the chat alongside the text.

## The other buttons

All the buttons work on a whole folder, not just one file.

- **Compress PDF** — shrinks a large PDF so it can be emailed. Set the target size
  in MB first.
- **PDF → Word** — an editable `.docx`. Good for a document you need to mark up;
  formatting is close but not identical to the original.
- **Word → PDF**, **PDF → Images**, **Images → PDF** — as they sound.

## If something goes wrong

Nothing you do here can alter your original files — FileForge only ever reads them
and writes new files elsewhere. If a conversion fails, the app shows the reason in
the black box at the bottom of the window. When converting a batch, one bad file is
skipped and the rest still finish.

Everything runs on your own computer. No document is uploaded anywhere until you
choose to put it into a Claude chat yourself.
