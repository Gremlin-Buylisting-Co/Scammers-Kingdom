# Scammer's Kingdom

To assist with Card Kingdom (CK) buylist orders. Import your collection from shoptcg.online and the tool works out which of your cards are worth selling to CK, and tells you when CK puts cards back on its buylist. It saves you the slow job of looking up each card one at a time when you are buylisting.

It is made for a group of friends who sell Magic cards to CK together. Each person runs it on their own PC. It answers one question: which of my cards should I send to CK, and which should I keep?

## Get it and start it

1. Go to the Releases page of this project on GitHub and download `ScammersKingdom.exe`.
2. Double-click it. A black window opens (leave it open) and your browser opens the tool.
3. The first time, Windows may warn that it does not know the program because it is not signed. Click **More info**, then **Run anyway**. This happens once.

Close the black window to stop the tool.

## The three steps

1. **Add cards.** Scan your cards in at shoptcg.online, export the file, and drop it on this page. You can also use **Import My Collection** to bring back a file you saved earlier with **Export My Collection**. You can choose to replace the cards that came from an earlier import or add to them. Cards you add by hand are always kept.
2. **Your binder.** Press **Update prices** first. Each card shows what CK pays in cash and in store credit, the market price, and what share of the market price CK pays (CK % of market), best first. Every card is Near Mint (NM) unless you change it to EX or VG. Use **+ Add a card** to add a card by hand, change quantities, conditions or printings, and use the search box to find a card in your binder. Every change saves at once and can be undone. A **Sell on CK** link opens Card Kingdom's buylist for that card in your browser.
3. **Plan a shipment.** Cards go to CK in bags of 50. Say how many bags you want to send and choose cash or credit. The tool picks the 50 times n cards with the highest CK % of market, shows each bag with what CK pays and the bag fee, and lets you download a CSV spreadsheet of the plan.

When CK puts a card of yours back on its buylist after an update, a badge at the top and a "Back" tag on the card tell you.

## The CK import file

Step 3 has a **Download CK import file** button. It makes one CSV file for the whole shipment in the format Card Kingdom's CSV import reads: no header row, and four columns for each card (card name, edition, foil, quantity). Card Kingdom grades your cards when they arrive, so the file has no condition column. To upload it, follow [Card Kingdom's CSV import page](https://www.cardkingdom.com/static/csvImport): choose your file, then match the four columns.

## Where your data is kept

Your binder, settings and prices are kept on your own PC in a folder called `ScammersKingdom-data`, next to the program. Nothing is kept in your browser, so the binder is still there the next time you open the tool. To move to a new version, put the new program in the same folder. To move to another PC, use Export My Collection and Import My Collection. After 14 days without an export, the tool offers a gentle reminder.

## What the tool talks to

Prices come from Card Kingdom's buylist and from the TCGplayer market price, which the tool gets through Scryfall. It makes those requests only when you press **Update prices**. Once a day it also asks GitHub whether a newer version exists, and shows a line if there is one (it never downloads anything by itself). It talks to nothing else. Card pictures that appear when you hover over a card are loaded by your browser straight from Scryfall.

Not affiliated with Card Kingdom.

## Running from source

You need Python 3 and nothing else (no extra packages). In this folder run:

```
python server.py
```

The tool opens in your browser at a local address (it starts at port 8901). It listens on your own computer only.

To run the checks: `python tests/run_tests.py`. To build the Windows program, install PyInstaller (`python -m pip install pyinstaller`) and run `build_exe.bat`.
