# DrainOps AI – Backend

FastAPI + SQLAlchemy (SQLite → PostGIS) + ONNX Runtime.

## Run the API
    pip install -r requirements.txt
    python load_registry.py        # loads the 101-drain registry (data/registry.xlsx) into the Digital Twin
    uvicorn app.main:app --reload  # docs at http://localhost:8000/docs
(`seed_demo.py` is the old simulated-data seeder; prefer the registry.)
`GET /health` reports `model_mode`. It says **demo_heuristic** until `models/drain_model.onnx` exists;
demo-mode scores are NOT real predictions.

## The 101-drain registry
`data/registry.xlsx` (West Bengal 80, Bihar 12, Jharkhand 9) seeds the Digital Twin: location, size, material,
health score, blockage description, forecast snapshot and risk score.
* **It has no photos, so it cannot train the vision model.** It can calibrate the risk engine and serve as the
  pilot audit list.
* `python training/validate_risk.py` compares the engine with the registry: ~93% priority-band agreement,
  all misses one band apart, rank correlation 0.997.
* `python training/registry_labels.py` writes `data/field_visit_sheet.csv` (a photo/depth-stick checklist for
  the 101 drains) and provisional labels. Photograph each drain as `data/photos/<drain_id>.jpg`;
  `prepare_manifest.py` then includes them. Confirm each tier on site before training on them.
* Data limits: one rainfall snapshot (8-25 mm), no cleaning dates, coordinates rounded to ~100 m,
  and 101 images is a validation/fine-tuning set, not enough to train from scratch.
* `GET /risk/ranking?source=registry` reproduces the registry's rainfall; the default uses the live forecast.

## Train the real model (Colab/Kaggle GPU, ~1–2 h)
1. Gather images into `data/raw/<source>/<Clear|Moderate|Critical>/`:
   * **Your own field photos (essential, aim for 1,500+).** No public dataset labels blockage severity for
     Indian open roadside drains, so local images are what make the model work in Kolkata.
   * **Public data to boost debris/clutter features** (check each licence, and relabel into the 3 tiers yourself):
     TACO (litter in context), TrashNet, Open Images (waste/litter classes), and sewer-inspection
     sets such as Sewer-ML (large, but pipe-interior defects, so use only after manual relabelling).
   * Optional `data/raw/debris_labels.csv` (path,plastic,silt,organic as 0/1).
2. `python training/prepare_manifest.py`
3. `pip install torch torchvision pandas scikit-learn onnx`
4. `python training/train.py --epochs 25` → writes `models/drain_model.onnx`
5. Read the per-class report. **Critical recall matters most**: missing a blocked drain is the costly error.
6. Copy the .onnx to the server's `models/` and restart.

## Continuous improvement
Supervisors correct scores via `POST /inspections/{id}/correct`; `training/export_corrections.py`
turns those into new labelled training data for the next retrain.

## Key endpoints
POST /inspect · GET /drains (GeoJSON) · GET /drains/{code}/history · GET /risk/ranking ·
POST /workorders/generate · POST /workorders/{id}/complete · GET /reports/ward/{ward}

## Known limits
* Photo scoring cannot see silt depth or covered culverts; pair with a depth-stick checklist.
* No authentication yet (add JWT/role-based access before any real deployment).
* Work-order batching is a simple north→south sweep, not true route optimisation.
