# Labels

`d3dplot.labels.label` builds a localized quantity label from the packaged
Matlab-derived variable mappings, translations, and unit table. It returns a
`Label` with separate `text`, `quantity`, and `unit` fields.

```python
from d3dplot.labels import LabelKind, label

result = label(
    "mesh2d_mor_bl",
    "nl",
    LabelKind.VALUE,
    datum="+NAP",
)
# result.text == "bodemhoogte [m+NAP]"
```

The supported languages are English (`en`), Dutch (`nl`), and Spanish (`es`).
`LabelKind` supports `VALUE`, `QUANTITY`, and `UNIT`, plus the comparison forms
`DIFFERENCE`, `BACKGROUND`, `STANDARD_DEVIATION`,
`DIFFERENCE_ABOVE_BACKGROUND`, `FILTERED`, `RELATIVE`, `PERCENTAGE`, and
`DOMINANT`. Reference-elevation datum suffixes are removed for differences,
standard deviations, filtered and dominant labels. `scale` selects a symbol
from `labels/data/units.csv`; it does not change numeric data. `fraction`
substitutes fraction-specific templates, or removes an optional fraction clause
when omitted.

## Project settings and extra dictionaries

Pass the project language, datum, and configured dictionary paths to the
builder:

```python
result = label(
    "mesh2d_mor_bl",
    project.language,
    LabelKind.VALUE,
    datum=project.labels.vertical_datum,
    extra_dictionaries=project.labels.extra_dictionaries,
)
```

Each extra dictionary is a UTF-8 CSV with the same columns as the packaged
`translations.csv`:

```csv
key,en,nl,es,unit_type
custom_velocity,custom velocity,aangepaste snelheid,velocidad personalizada,L/T
```

All three translations and a unit type present in `units.csv` are required.
Rows are applied in configuration order; a later dictionary can override a
packaged or earlier translation. A new variable can use its normalized variable
name as the translation key. For an unregistered variable, pass its NetCDF
`long_name` and `units` as `long_name` and `netcdf_units`; the builder emits a
`UserWarning` and uses that metadata.
