<p align="center">
  <a href="https://github.com/EnAccess/oseas26-building-footprint-energy-demand">
    <img
      src="https://drive.google.com/uc?id=1gtL_p7l3HbOcCzc09A7KW5d7B5qn-BDs"
      alt="Building Footprint - Spatial-Temporal Building Energy Demand"
      width="640"
    >
  </a>
</p>
<p align="center">
    October 26-27 | Open Source in Energy Access Symposium Hackathon | Kigali, Rwanda
</p>

---

# Building Footprint - Spatial-Temporal Building Energy Demand

An end-to-end workflow that leverages existing building footprint datasets to
generate high-resolution, spatially explicit energy demand intelligence.

## Abstract and goal

This end-to-end workflow would leverage existing building footprint datasets to
generate high-resolution, spatially explicit energy demand intelligence. The
process could integrate building footprint data with complementary geospatial and
contextual information to classify structures by use type, such as residential,
commercial, institutional, or productive-use buildings.

Appliance-level load profiles are then modeled and forecast for each building
category using tools such as RAMP (Remote Appliance Load Profiles), producing
temporally resolved demand profile estimates based on climate conditions,
appliance ownership data, and behavioral assumptions that capture daily and
seasonal variations in energy use.

The resulting analysis creates a more detailed understanding of current and
potential energy needs at the individual building level, enabling more accurate
electrification planning, demand aggregation, and infrastructure investment
decisions.

## Expected outcomes

An **Energy-Classified Building Dataset** in GIS format, where individual building
footprints are enriched with energy-related attributes such as:

- Estimated Peak Load
- Annual Energy Consumption
- Cooling Demand Category (Low, Medium, or High)
- Load Profile Characteristics
- Productive-Use Viability

This dataset provides a scalable foundation for energy access planning, demand
forecasting, mini-grid design, and identification of high-impact electrification
opportunities.

## Required knowledge

### Stack

- **PostgreSQL + PostGIS** for storing and querying building footprints and
  spatial datasets.
- **GeoParquet, GeoPandas, and DuckDB** for scalable geospatial analytics.
- **Time-series demand modeling tools** such as RAMP for generating
  appliance-level energy consumption and load forecasts.
- **ML** — Scikit-learn, XGBoost, LightGBM, PyTorch, or TensorFlow for building
  classification, appliance ownership, and demand forecasting.
- **Building footprint datasets** from sources such as Open Buildings,
  OpenStreetMap, Google Open Buildings, and Microsoft Building Footprints.
- **Localized electricity and cooling demand profiles**; appliance ownership and
  usage surveys; climate and weather datasets; demographic and socioeconomic
  indicators — from sources such as ERA5, NASA POWER, WorldClim, and the
  Copernicus Climate Data Store.

### Programming languages

- Python
- SQL

### Helpful experiences

- Experience with Python, energy modelling, and remote sensing analytics.
- Skills in geospatial data analysis using PostGIS, QGIS, GeoJSON, GeoParquet, or
  spatial SQL.

## Person of contact supporting this challenge

- Santiago Sinclair Lecaros
- Akansha Saklani

## Getting started

- Join the OSEAS Discord server: <https://community.oseas.org/>
- Introduce yourself in the `#introductions` channel and join the relevant
  channels for this challenge.
