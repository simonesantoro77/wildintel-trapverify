# <img src="img/wildIntel_logo.webp" alt="WildINTEL Logo" height="60"> CamTrap Verify

![Python](https://img.shields.io/badge/python-3.13-blue.svg)
![License](https://img.shields.io/badge/license-GPLv3-blue.svg)
[![CI](https://github.com/wildintelproject/wildintel-trapverify/actions/workflows/ci.yml/badge.svg)](https://github.com/wildintelproject/wildintel-trapverify/actions/workflows/ci.yml)
[![WildINTEL](https://img.shields.io/badge/WildINTEL-v1.0-blue)](https://wildintel.eu/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.138-009688)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB)](https://react.dev/)
[![uv](https://img.shields.io/badge/uv-package_manager-5C4EE5)](https://github.com/astral-sh/uv)

<hr>

## Web tool for the iterative verification of camera trap classifications

**CamTrap Verify** is an open-source web application designed to help ecologists and wildlife researchers review and validate species detections produced by AI classifiers or citizen-science platforms. It consumes any directory in [CamtrapDP v1.0](https://camtrap-dp.tdwg.org/) format and guides the expert through a structured, iterative review workflow that minimises the total number of images that need to be inspected.

In each round, the tool presents the **highest-confidence detection event** not yet reviewed for every combination of site × sampling occasion × species. Confirming a detection event closes that cell; rejecting it queues the next-best detection event for the following round. This ensures that expert effort is always directed where it matters most — without having to look at every single image.

Once the review is complete, **CamTrap Verify** exports a verified CamtrapDP package with confirmed observations tagged as human classifications, together with detection histories and camera-operation matrices ready to feed directly into species occupancy models.

<img src="img/welcome-screen.png" alt="Welcome screen" height="200"> <img src="img/setup-step1-directory.png" alt="Setup wizard" height="200"> <img src="img/species-index.png" alt="Species index" height="200"> <img src="img/gallery-overview.png" alt="Image gallery" height="200">

## ✨ Features

**Data ingestion**
- Accepts any [CamtrapDP v1.0](https://camtrap-dp.tdwg.org/) directory directly
- Built-in converter for [DeepFaune](https://www.deepfaune.cnrs.fr/) CSV exports
- Generic CSV importer with configurable column mapping and interactive species-name editor for any AI classifier output

**Review workflow**
- Iterative review by rounds: for each site × sampling-occasion × species cell, the highest-confidence detection event is always shown first
- Confirming a detection event closes the cell; rejecting it queues the next-best detection event in the following round, minimising total expert effort
- Optional full-event mode: shows all frames from the same deployment captured between the first and last detection of the target species, including frames not labelled as that species
- Optional extended confirmation: marks all observations in the confirmed burst — not just the highest-confidence frame — as human-verified in the output
- "Unsure" decisions: occasions that end the rounds with an unsure event stay pending expert review (NA in the verified history, `pending` in `cell_status_*.csv`) instead of becoming a non-detection
- Export / import a session to continue the review on another computer (Windows ↔ macOS), with the data and photo folders relinked there

**Image viewer**
- Image viewer with frame-by-frame browsing, zoom, pan, rotation, tonal inversion and full keyboard navigation, one location per page so rounds with thousands of cards stay fast
- Brightness and contrast controls
- Full-screen lightbox

**Output**
- Exports `camtrap_dp_verified/` with confirmed observations tagged as human classifications; configurable `classifiedBy` label
- Generates naive vs. verified detection histories and camera-operation matrices ready for occupancy models (`occupancy_inputs/`)

**General**
- Bilingual interface (Spanish / English)
- Docker deployment or hot-reload development mode

## 📋 Requirements

- Python 3.13+ with [uv](https://github.com/astral-sh/uv)
- Node.js 18+ / npm 9+
- Docker + Compose (production only)

## 📚 Documentation

Full documentation available at:
**https://wildintelproject.github.io/wildintel-trapverify/**
  
## 🚀 Quick start

```bash
git clone https://github.com/wildintelproject/wildintel-trapverify.git
cd wildintel-trapverify

# Install dependencies
./setup.sh          # Windows (PowerShell): .\setup.ps1

# Start in development mode
uv run cli  dev
```

The browser will open at `http://localhost:5173`. See the [user manual](https://wildintelproject.github.io/wildintel-trapverify/latest/user-manual/) for a full walkthrough using the included example dataset.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

To report a bug or request a new feature, please open an [issue](https://github.com/wildintelproject/wildintel-trapverify/issues).

## 📝 License

This project is licensed under the GNU General Public License v3.0 or later - see the [LICENSE](LICENSE) file for details.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License
as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.


## 🏛️ Funding

This work is part of the [WildINTEL project](https://wildintel.eu/), funded by the [Biodiversa+](https://www.biodiversa.eu/) Joint Research Call 2022-2023 "Improved
transnational monitoring of biodiversity and ecosystem change for science and society (BiodivMon)". Biodiversa+ is the
European co-funded biodiversity partnership supporting excellent research on biodiversity with an impact for policy and
society. Biodiversa+ is part of the European Biodiversity Strategy for 2030 that aims to put Europe's biodiversity on a
path to recovery by 2030 and is co-funded by the European Commission.

WildINTEL has been co-funded by the [European Commission](https://commission.europa.eu/) (GA No. 101052342) and the following funding organisations: [Agencia Estatal de Investigación](https://www.aei.gob.es/) (Spain, PCI2023-145963-2, PCI2024-153489), [National Science Centre](https://www.ncn.gov.pl/?language=en) (Poland, UMO-2023/05/Y/NZ8/00104), the [Research Council of Norway](https://www.forskningsradet.no/en/) (Norway, NFR350962) and the [German Research Foundation](https://www.dfg.de/en/) (Germany).