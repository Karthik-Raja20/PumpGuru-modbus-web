"""
================================================================================
PUMPGURU REPORT BRANDING — EDIT THIS FILE TO REBRAND EVERYTHING
================================================================================
This is the ONLY file you need to touch to change branding across the PDF
report, the Excel report, and the web dashboard logo. Every other file reads
its logo path and colors from here — nothing is hardcoded elsewhere.

--------------------------------------------------------------------------------
WHAT'S ALREADY SET UP
--------------------------------------------------------------------------------
Your logo (Aventek) is already in place:
    reports/assets/Aventek_logo_1.png     <- black logo, for white/light backgrounds
    reports/assets/Aventek_logo_white.png <- white logo, for dark backgrounds
                                              (auto-generated from your black
                                              original — same shape, recolored)

--------------------------------------------------------------------------------
HOW TO REPLACE THE LOGO WITH A DIFFERENT ONE LATER
--------------------------------------------------------------------------------
1. Save your new logo as a PNG with a TRANSPARENT background (not white) into
   reports/assets/ — this matters because it needs to look correct on both
   white paper (reports) and dark screens (dashboard dark mode).
2. Update LOGO_DARK_TEXT_PATH below to point to that new file, if the logo's
   ink color is dark/black (most common).
   If your new logo's ink color is already light/white, put it in
   LOGO_LIGHT_TEXT_PATH instead and set LOGO_DARK_TEXT_PATH to None.
3. If you need BOTH a light and dark version and only have one, run:
       python reports/generate_logo_variants.py
   which recolors your logo the same way the current white variant was made.
================================================================================
"""

import os
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "images")
# ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# --- Logo files ---------------------------------------------------------------
# "dark text" = logo ink is a dark/black color -> use on WHITE/light backgrounds
#               (PDF reports, Excel reports, web dashboard LIGHT theme)
# "light text" = logo ink is white/light color -> use on DARK backgrounds
#               (web dashboard DARK theme)
LOGO_DARK_TEXT_PATH = os.path.join(ASSETS_DIR, "Aventek_logo_1.png")
LOGO_LIGHT_TEXT_PATH = os.path.join(ASSETS_DIR, "Aventek_logo_1.png")

# --- Company / report identity -------------------------------------------------
COMPANY_NAME = "Aventek"
REPORT_TITLE = "Pump Protection & Fault Analysis Report"
COMPANY_TAGLINE = ""   # optional, e.g. "Industrial Pump Automation" — leave "" for none

# --- Brand colors (hex, no '#') -------------------------------------------------
# These drive every table header, chart color, and accent band in both the
# PDF and Excel reports. Change these to match your actual brand palette;
# everything downstream updates automatically, nothing else needs editing.
COLOR_PRIMARY_DARK = "142433"    # cover band / header background (near-black navy)
COLOR_PRIMARY = "1C7293"         # main accent — table headers, chart line 1
COLOR_PRIMARY_DEEP = "0F4C5C"    # secondary accent — chart line 2
COLOR_AMBER = "F2A541"           # warnings / near-miss indicators
COLOR_GREEN = "2E9E6D"           # good / healthy indicators
COLOR_RED = "D9534F"             # fault / critical indicators
COLOR_LIGHT_BG = "E8F1F5"        # table zebra-striping background
COLOR_INK = "1A2530"             # body text
COLOR_MUTED = "5C6B75"           # secondary/muted text

# --- Logo sizing in reports -----------------------------------------------------
# Width in inches for the PDF header logo (height auto-scales to preserve
# the logo's real aspect ratio — do not hardcode a height here).
PDF_LOGO_WIDTH_INCHES = 1.6

# Width in Excel column-units for the embedded worksheet logo.
EXCEL_LOGO_WIDTH_PX = 180
