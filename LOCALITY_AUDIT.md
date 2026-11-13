# Locality count audit — 2026-11-13

The old 399 counter counted display labels, including streets, spelling variants and geocoder landmarks. It did not count canonical settlements.

The corrected snapshot has 337 identified locality codes, 1,231 map pins and 1,236 active CRM deals. All 1,227 previously published deal IDs and coordinates are preserved; deals 2810–2813 were added. Nahariya is one code (9100), with 21 deals.

This is the exact count of identified localities, not a certified total for every CRM deal. Six mapped deals have ambiguous or incomplete locality information: 59, 73, 86, 1875, 1898, 2788. Five other deals have no person/address: 114, 2229, 2409, 2800, 2809. These cannot create an invented locality. Resolve their addresses in CRM to finish the total across all deals. Reviewed exceptions are guarded by person ID plus exact original address, so changing an address invalidates an old exception.

Identity uses the original CRM address, structured locality and a pinned official settlement-code dictionary. Google formatted addresses can drop or replace towns, so geocoder labels and roof precision never determine locality counts. Map geometry is preserved; this change does not certify old geocoding coordinates.

Every incremental sync refreshes locality identity even for unchanged addresses. The UI groups by locality code. Publication validates code/name consistency, deal identities and every existing roof coordinate. Twenty regression tests cover duplicate streets, multilingual aliases, lossy formatted addresses, ambiguous locations and unchanged-address refresh.
