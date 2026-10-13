# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import urllib.parse
import urllib.request

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.load_memory_tool import LoadMemoryTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore
from google.genai import types


MODEL = "gemini-3.8-flash"

# Hardcoded project ID as required to avoid project number issues on Agent Platform
PROJECT_ID = "qwiklabs-gcp-04-e9c25e635f84"
COLLECTION_NAME = "box_trucks"


def _get_db() -> firestore.Client:
    """Returns a Firestore client configured with the hardcoded project ID."""
    return firestore.Client(project=PROJECT_ID)


def _enrich_truck_metadata(data: dict) -> dict:
    """Enriches truck metadata with calculated per-mile rates and imperial equivalents for memory indexing."""
    if not data:
        return data
    res = dict(data)
    if "rate_per_km" in res and res["rate_per_km"] is not None:
        res["rate_per_mile"] = round(res["rate_per_km"] * 1.60934, 2)
    if "odometer_km" in res and res["odometer_km"] is not None:
        res["odometer_miles"] = round(res["odometer_km"] * 0.621371, 1)
    if "capacity_kg" in res and res["capacity_kg"] is not None:
        res["capacity_lbs"] = round(res["capacity_kg"] * 2.20462)
    return res


def list_box_trucks(status_filter: str = "") -> list[dict]:
    """Lists box trucks in the fleet from Firestore, optionally filtered by operational status.

    Args:
        status_filter: Optional filter ('available', 'dispatched', 'maintenance', or empty string for all).

    Returns:
        A list of dictionaries containing box truck details, per-km/per-mile rates, and current status.
    """
    db = _get_db()
    collection = db.collection(COLLECTION_NAME)

    query = collection
    if status_filter and status_filter.strip().lower() in ["available", "dispatched", "maintenance"]:
        query = query.where("status", "==", status_filter.strip().lower())

    results = []
    for doc in query.stream():
        data = doc.to_dict()
        results.append(_enrich_truck_metadata(data))

    return results


def get_truck_details(truck_id: str) -> dict:
    """Fetches real-time status and operational metrics for a specific box truck from Firestore.

    Args:
        truck_id: The ID of the truck (e.g., 'BT-101', 'BT-102').

    Returns:
        A dictionary with truck specs, odometer, rate per km, rate per mile, driver, and active trip details.
    """
    db = _get_db()
    clean_id = truck_id.strip().upper()
    doc = db.collection(COLLECTION_NAME).document(clean_id).get()

    if not doc.exists:
        return {"error": f"Truck with ID '{clean_id}' was not found in Firestore fleet records."}

    return _enrich_truck_metadata(doc.to_dict())


def assign_truck_to_dispatch(
    truck_id: str,
    trip_id: str,
    destination: str,
    route_distance_km: float,
    driver_name: str = ""
) -> dict:
    """Assigns an available box truck to a partner company per-km dispatch trip and updates Firestore.

    Args:
        truck_id: The ID of the truck to assign (e.g., 'BT-101').
        trip_id: The partner trip or consignment ID (e.g., 'TRIP-905').
        destination: Dropoff destination for the route.
        route_distance_km: Estimated distance of the trip in kilometers.
        driver_name: Name of the driver assigned to this dispatch (optional).

    Returns:
        A dictionary confirming the assignment with estimated trip revenue based on per-km rate.
    """
    db = _get_db()
    clean_id = truck_id.strip().upper()
    doc_ref = db.collection(COLLECTION_NAME).document(clean_id)
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": f"Truck '{clean_id}' does not exist."}

    truck = doc.to_dict()
    if truck.get("status") == "maintenance":
        return {"error": f"Truck '{clean_id}' is currently in maintenance and cannot be dispatched."}
    if truck.get("status") == "dispatched":
        return {"error": f"Truck '{clean_id}' is already dispatched on trip '{truck.get('active_trip', {}).get('trip_id')}'."}

    rate_per_km = float(truck.get("rate_per_km", 1.75))
    est_revenue = round(route_distance_km * rate_per_km, 2)
    assigned_driver = driver_name.strip() if driver_name.strip() else truck.get("driver", "Unassigned")

    active_trip = {
        "trip_id": trip_id.strip().upper(),
        "destination": destination.strip(),
        "route_distance_km": float(route_distance_km),
        "dispatched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "estimated_revenue_usd": est_revenue,
    }

    doc_ref.update({
        "status": "dispatched",
        "driver": assigned_driver,
        "active_trip": active_trip,
    })

    return {
        "status": "success",
        "message": f"Truck {clean_id} successfully assigned to trip {trip_id}.",
        "truck_id": clean_id,
        "driver": assigned_driver,
        "destination": destination,
        "distance_km": route_distance_km,
        "contract_rate_per_km": rate_per_km,
        "estimated_revenue_usd": est_revenue,
    }


def complete_dispatch_trip(
    truck_id: str,
    actual_km: float,
    fuel_cost_usd: float = 0.0
) -> dict:
    """Marks a completed partner dispatch trip in Firestore, updates odometer, and computes earnings.

    Args:
        truck_id: The ID of the truck completing the trip (e.g., 'BT-102').
        actual_km: The total kilometers driven on the trip.
        fuel_cost_usd: Total fuel expense in USD incurred during the trip (optional).

    Returns:
        A dictionary with trip summary, updated odometer, gross revenue, fuel expense, and net profit.
    """
    db = _get_db()
    clean_id = truck_id.strip().upper()
    doc_ref = db.collection(COLLECTION_NAME).document(clean_id)
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": f"Truck '{clean_id}' does not exist."}

    truck = doc.to_dict()
    current_odometer = float(truck.get("odometer_km", 0.0))
    rate_per_km = float(truck.get("rate_per_km", 1.75))
    new_odometer = round(current_odometer + actual_km, 1)

    gross_revenue = round(actual_km * rate_per_km, 2)
    net_profit = round(gross_revenue - fuel_cost_usd, 2)

    active_trip = truck.get("active_trip") or {}
    completed_trip_id = active_trip.get("trip_id", "TRIP-COMPLETED")

    next_service = float(truck.get("next_service_odometer_km", 0.0))
    service_alert = new_odometer >= next_service if next_service else False

    doc_ref.update({
        "status": "available",
        "odometer_km": new_odometer,
        "active_trip": None,
    })

    return {
        "status": "success",
        "message": f"Trip {completed_trip_id} completed for truck {clean_id}.",
        "truck_id": clean_id,
        "actual_km": actual_km,
        "contract_rate_per_km": rate_per_km,
        "gross_revenue_usd": gross_revenue,
        "fuel_expense_usd": fuel_cost_usd,
        "net_profit_usd": net_profit,
        "new_odometer_km": new_odometer,
        "service_recommended": service_alert,
    }


def update_truck_maintenance(
    truck_id: str,
    in_maintenance: bool,
    maintenance_notes: str = ""
) -> dict:
    """Sets a truck into maintenance mode or returns it to available status in Firestore.

    Args:
        truck_id: The ID of the truck (e.g., 'BT-104').
        in_maintenance: True to place in maintenance; False to return to available.
        maintenance_notes: Description of repairs, inspection, or parts replaced.

    Returns:
        A dictionary with updated maintenance status.
    """
    db = _get_db()
    clean_id = truck_id.strip().upper()
    doc_ref = db.collection(COLLECTION_NAME).document(clean_id)
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": f"Truck '{clean_id}' does not exist."}

    new_status = "maintenance" if in_maintenance else "available"
    doc_ref.update({
        "status": new_status,
        "maintenance_notes": maintenance_notes if in_maintenance else None,
    })

    return {
        "status": "success",
        "truck_id": clean_id,
        "new_status": new_status,
        "maintenance_notes": maintenance_notes,
    }


def calculate_route_distance(origin: str, destination: str) -> dict:
    """Calculates driving distance in kilometers and driving duration between two locations.

    Args:
        origin: Starting city, warehouse address, or postal code (e.g. 'Newark, NJ').
        destination: Destination city, warehouse address, or postal code (e.g. 'Philadelphia, PA').

    Returns:
        A dictionary with distance in km, driving duration in minutes, and route status.
    """
    def _geocode(address: str) -> tuple[float, float] | None:
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address)}&format=json&limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": "FleetDispatchAgent/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode())
                if data:
                    return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception:
            pass
        return None

    origin_coords = _geocode(origin)
    dest_coords = _geocode(destination)

    if not origin_coords or not dest_coords:
        return {
            "status": "error",
            "message": f"Could not geocode locations: origin '{origin}', destination '{destination}'.",
        }

    try:
        osrm_url = (
            f"http://router.project-osrm.org/route/v1/driving/"
            f"{origin_coords[1]},{origin_coords[0]};{dest_coords[1]},{dest_coords[0]}?overview=false"
        )
        req = urllib.request.Request(osrm_url, headers={"User-Agent": "FleetDispatchAgent/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            route = data["routes"][0]
            distance_km = round(route["distance"] / 1000.0, 1)
            duration_mins = round(route["duration"] / 60.0, 1)
            return {
                "status": "success",
                "origin": origin,
                "destination": destination,
                "distance_km": distance_km,
                "duration_minutes": duration_mins,
                "duration_hours": round(duration_mins / 60.0, 1),
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Routing service error: {str(e)}",
        }


async def generate_memories_callback(callback_context: CallbackContext):
    """Stores durable preferences, driver habits, and dispatch details across sessions to Memory Bank."""
    await callback_context.add_session_to_memory()
    return None


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are a Fleet & Vendor Dispatch Manager AI assistant.
You help a logistics business owner manage their box truck fleet and partner per-km vendor dispatches.
Your operations are backed by a real Google Cloud Firestore database ('box_trucks' collection) and long-term Memory Bank.

Memory & Fleet Tracking Guidelines:
- Per-km and Per-mile Rates:
  Always remember, track, and recall both the per-km rate and the equivalent per-mile rate ($/mile = $/km * 1.60934) for each vehicle and for partner contracts. When quoting or recording rates, present both $/km and $/mile so both units are indexed into durable memory.
- Box-Truck Metadata:
  Remember all metadata for each box truck: truck ID, model/make, body length, payload capacity (kg and lbs), odometer reading (km and miles), assigned driver, fuel level, service milestones (last service and next service thresholds), and active trip status. Always reference these remembered specs when planning dispatches.
- Memory Tools:
  - PreloadMemoryTool automatically loads user preferences and previous facts at turn start.
  - LoadMemoryTool (load_memory) allows you to actively query long-term memory for specific owner instructions, custom rates, or historical truck data.

Live Fleet Tools:
- list_box_trucks: To view the fleet roster or filter by status ('available', 'dispatched', 'maintenance').
- get_truck_details: To check truck specs, odometer, rate per km/mile, driver, and active trip details.
- calculate_route_distance: To lookup actual driving distance in km and duration between two locations.
- assign_truck_to_dispatch: To dispatch an available truck to a partner trip, calculate projected per-km revenue, and set driver.
- complete_dispatch_trip: To mark a trip completed, record actual km and fuel expenses, calculate profit, and update odometer.
- update_truck_maintenance: To place a truck into service/maintenance or clear it back to available.

When answering, provide crisp, well-structured summaries with financial breakdowns ($/km, $/mile, revenue, net profit) whenever relevant.""",
    tools=[
        PreloadMemoryTool(),
        LoadMemoryTool(),
        list_box_trucks,
        get_truck_details,
        calculate_route_distance,
        assign_truck_to_dispatch,
        complete_dispatch_trip,
        update_truck_maintenance,
    ],
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
