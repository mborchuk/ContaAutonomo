# AEAT import files for the drafts

Every draft page has an **⬇ AEAT file (not validated)** button. It downloads
the draft as the text file the AEAT Sede reads, built by `aeat_files.py` from
the official record layout of the exercise. The figures are the draft's; the
file adds no tax rule. Checked on 16 November 2026.

## Supported models

| Model | How the Sede takes the file | Official layout (source) | In the app | Validated |
|---|---|---|---|---|
| Modelo 303 | Form → *Importar* (page records `<T303…>`) | DR303e26v101, ejercicio 2026 and later ([AEAT, Diseños de registro 300–399](https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-300-399.html)) | Pages 01, 03 and DID, from 2026 | **Not validated** |
| Modelo 130 | Form → *Importar*; the layout's note asks for a file named `130.txt` | DR130e15v12 ([AEAT, Diseños de registro 100–199](https://sede.agenciatributaria.gob.es/Sede/ayuda/disenos-registro/modelos-100-199.html)) | Page 01 | **Not validated** |
| Modelo 390 | Form → *Importar* | dr390e2026, ejercicio 2026 and later | Pages 01–08, from 2026 | **Not validated** |
| Modelo 349 | *Presentación mediante fichero* (500-character records) | DR_Anexo_349, Orden HAC/174/2020 | Key S lines of the quarter | **Not validated** |
| Modelo 347 | *Presentación mediante fichero* (500-character records) | 347, ejercicio 2025 and later (Orden HAC/1431/2025) | Keys A and B of the 347 check | **Not validated** |

**Not validated** means that no generated file has been through the AEAT's
validation from this repository. The Sede validates on import, and only with
the taxpayer's certificate or Cl@ve; there is no public pre-validation
service. Import the file, read the messages, correct the form, then file. A
model of a year before its layout (for example Modelo 303 of 2025) is refused
with a message.

Out of scope: Modelo 100 (Renta WEB has no file import for activity income)
and Modelo 111. The app prepares neither.

## How the files are built

- `aeat_layouts/*.json` hold the page layouts of 303, 130 and 390, extracted
  from the official spreadsheets: position, length, type (`An`, `A`, `Num`,
  `N`), the casilla the field carries, and its constant. The file names say
  the first exercise they apply to; add a new file and a row in
  `LAYOUT_FILES` when the AEAT publishes a new layout.
- Text fields: upper case, no accents (Ñ and Ç kept), letters, digits and
  blanks only, left aligned. Numbers: cents, right aligned with zeros; a
  negative signed field starts with `N`. Encoding ISO-8859-1. The 349 and
  347 records end with CR LF.
- A field the app has no data for is blank or zero.

### Modelo 303

The draft's general-rate lines (boxes 01/03 on the draft page) are the 21 %
lines. The 2026 layout numbers them **[07]/[08]/[09]**: [01]–[03] are 4 %,
[04]–[06] 10 %. The file puts the 21 % base and cuota in [07] and [09] and the
rate 21.00 in [08]. Page 03: [59], [120], [110], [78], [87]; [64] = [66] =
[46]; [65] = 100 %; [69] = [66] − [78]; [71]. Tipo de declaración: `I` when
there is something to pay, `N` at zero, `C` (compensate) when negative —
change it to `D` in the form to ask for a refund in 4T. Identification flags:
not foral, no monthly refund register, régimen general only, no criterio de
caja, no SII, no option for (and no revocation of) the prorrata especial
(`2`; these two take only 1 or 2 in every period); in 4T: files Modelo 390,
volume of operations not zero (`0` in 1T–3T). A numeric field never holds a
blank — the Sede rejects the whole file (`FRECH`, "Caracteres no válidos").
Every "Tipo %" casilla carries its fixed rate, also on an empty line ([169]
only admits 0,50: E010541); the layout holds them as constants, and the [169]
constant, missed by `build.py`, was added by hand on 2026-11-20.

### Modelo 130

Boxes [01]–[06] from the draft. [07] = [04] − [05] − [06] (may be negative);
the draft's box 07 already applies the art. 110.3.c reduction, which the file
shows in [13]; [12], [14], [17] and [19] follow. Tipo de declaración `I` or
`N` (negative or zero).

### Modelo 390

The casillas of the 390 draft go to their pages (02: 05, 06, 551, 552, 27, 28,
33, 34; 02 bis: 47; 03: 48–51; 04: 597, 598, 58, 59, 64, 65; 06: 84–86, 95,
97, 99, 103, 110, 108). Page 01 carries the identification; the "files Modelo
347" mark follows the 347 check. **Complete in the form:** activity
description, activity code and IAE epigraph (page 01), and the breakdown of
deductible VAT by rate that page 03 asks for besides the totals.

### Modelo 349 and 347

One type 1 record (declarant, totals) and one type 2 record per line. The
declaration number is the model code followed by the year and a sequence.
347: Spanish counterparties carry their NIF and the province from the first
two digits of their postal code; others get province 99 and the country from
the first two letters of their VAT number; non-EU reverse-charge purchases are
marked "inversión del sujeto pasivo". A line without a postal code or country is
listed as a warning on the 347 page and must be completed in the Sede.

### Names

The Sede wants individuals as "first surname, second surname, name". The app
stores one owner name: written "Surnames, Name" it is split at the comma,
otherwise the first word is taken as the name. Check the name fields after
importing.
