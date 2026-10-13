# My agent: Fleet & Vendor Dispatch Manager
One-liner: A conversational agent that helps a fleet owner manage box trucks, track partner company per-km dispatch trips, and compute trip earnings and vehicle readiness with a catalog of box trucks and trip logs.

Tool coverage:
- Memory: Remembers owner preferences, partner contract rate per km, driver assignments, and vehicle service thresholds
- Tools: assign_truck_to_trip (assigns truck & driver to partner dispatch), complete_trip (logs odometer, km driven, and fuel expenses), get_truck_status (checks availability and maintenance status)
- Catalog/UI: Box truck fleet roster and active dispatch trips rendered as status cards and tables
- Image gen: Vehicle inspection sign-off cards and cargo loading layout previews
- Sandbox: Trip payout calculation (total km * contract rate - fuel & tolls), fuel efficiency (km per liter), and net profit per trip

Recommended for every project: memory, storage, tools, image generation, A2UI
Agent-specific / stretch (pick what fits): Code sandbox for per-km earnings and profit margin calculation, Google Maps API for route distance verification, Cloud Trace for latency monitoring
