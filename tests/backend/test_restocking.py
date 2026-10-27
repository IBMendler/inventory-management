"""
Tests for restocking API endpoints.
"""
import pytest


def first_candidates(client, count=2):
    """Return the first `count` restocking candidates."""
    candidates = client.get("/api/restocking/candidates").json()
    assert len(candidates) >= count
    return candidates[:count]


def order_payload(candidates, budget=1_000_000):
    """Build a valid order body that restocks each candidate's full shortfall."""
    return {
        "items": [{"sku": c["sku"], "quantity": c["shortfall"]} for c in candidates],
        "budget": budget,
    }


class TestRestockingCandidatesEndpoint:
    """Test suite for the restocking candidates endpoint."""

    def test_get_candidates(self, client):
        """Test getting restocking candidates."""
        response = client.get("/api/restocking/candidates")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        for field in ["sku", "name", "forecasted_demand", "quantity_on_hand",
                      "shortfall", "unit_cost", "lead_time_days", "total_cost"]:
            assert field in first

    def test_candidates_have_positive_shortfall(self, client):
        """Test that only items short on stock are returned."""
        for item in client.get("/api/restocking/candidates").json():
            assert item["shortfall"] > 0
            assert item["shortfall"] == item["forecasted_demand"] - item["quantity_on_hand"]

    def test_candidates_sorted_by_shortfall_desc(self, client):
        """Test that the largest shortfall comes first."""
        shortfalls = [c["shortfall"] for c in client.get("/api/restocking/candidates").json()]
        assert shortfalls == sorted(shortfalls, reverse=True)

    def test_candidates_total_cost_calculation(self, client):
        """Test that total cost is shortfall times unit cost."""
        for item in client.get("/api/restocking/candidates").json():
            assert abs(item["total_cost"] - item["shortfall"] * item["unit_cost"]) < 0.01

    def test_candidates_only_include_forecasted_skus(self, client):
        """Test that every candidate comes from the demand forecast."""
        forecast_skus = {f["item_sku"] for f in client.get("/api/demand").json()}
        for item in client.get("/api/restocking/candidates").json():
            assert item["sku"] in forecast_skus

    def test_candidates_lead_time_is_positive(self, client):
        """Test that lead time is always populated."""
        for item in client.get("/api/restocking/candidates").json():
            assert isinstance(item["lead_time_days"], int)
            assert item["lead_time_days"] > 0


class TestRestockingCandidatesFilters:
    """Test suite for warehouse and category filters on restocking candidates."""

    def test_filter_by_warehouse(self, client):
        """Test that candidates are limited to SKUs stocked in the warehouse."""
        inventory = client.get("/api/inventory?warehouse=London").json()
        london_skus = {i["sku"] for i in inventory}

        data = client.get("/api/restocking/candidates?warehouse=London").json()
        assert len(data) > 0
        for item in data:
            assert item["sku"] in london_skus

    def test_filter_by_category(self, client):
        """Test that candidates are limited to the category."""
        inventory = client.get("/api/inventory?category=Controllers").json()
        skus = {i["sku"] for i in inventory}

        data = client.get("/api/restocking/candidates?category=Controllers").json()
        assert len(data) > 0
        for item in data:
            assert item["sku"] in skus

    def test_filters_narrow_results(self, client):
        """Test that a filter returns a subset of the unfiltered candidates."""
        all_skus = {c["sku"] for c in client.get("/api/restocking/candidates").json()}
        london = {c["sku"] for c in client.get("/api/restocking/candidates?warehouse=London").json()}
        assert london < all_skus

    def test_all_filter_values_are_ignored(self, client):
        """Test that 'all' behaves like no filter."""
        unfiltered = client.get("/api/restocking/candidates").json()
        filtered = client.get("/api/restocking/candidates?warehouse=all&category=all").json()
        assert filtered == unfiltered

    def test_filter_with_no_match_returns_empty(self, client):
        """Test that a warehouse with no matching stock returns an empty list."""
        response = client.get("/api/restocking/candidates?warehouse=Nowhere")
        assert response.status_code == 200
        assert response.json() == []


class TestCreateRestockingOrderEndpoint:
    """Test suite for placing restocking orders."""

    def test_create_order(self, client):
        """Test placing a valid order."""
        candidates = first_candidates(client)
        response = client.post("/api/restocking/orders", json=order_payload(candidates))
        assert response.status_code == 201

        order = response.json()
        assert order["status"] == "Submitted"
        assert order["order_number"].startswith("RST-")
        assert len(order["items"]) == len(candidates)

    def test_create_order_total_and_lead_time(self, client):
        """Test total is computed server-side and lead time is the longest item."""
        candidates = first_candidates(client)
        order = client.post("/api/restocking/orders", json=order_payload(candidates)).json()

        expected_total = sum(c["total_cost"] for c in candidates)
        assert abs(order["total_value"] - expected_total) < 0.01
        assert order["lead_time_days"] == max(c["lead_time_days"] for c in candidates)

    def test_create_order_expected_delivery_after_order_date(self, client):
        """Test expected delivery is later than the order date."""
        order = client.post(
            "/api/restocking/orders", json=order_payload(first_candidates(client, 1))
        ).json()
        assert order["expected_delivery"] > order["order_date"]
        assert "T" in order["expected_delivery"]

    def test_create_order_ignores_client_supplied_cost(self, client):
        """Test that extra cost fields in the body cannot change the total."""
        candidate = first_candidates(client, 1)[0]
        payload = order_payload([candidate])
        payload["items"][0]["unit_cost"] = 0.01
        order = client.post("/api/restocking/orders", json=payload).json()
        assert abs(order["total_value"] - candidate["total_cost"]) < 0.01

    def test_create_order_over_budget(self, client):
        """Test that an order exceeding the budget is rejected."""
        candidates = first_candidates(client)
        response = client.post("/api/restocking/orders", json=order_payload(candidates, budget=1))
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()

        # Nothing should have been stored
        assert client.get("/api/restocking/orders").json() == []

    def test_create_order_exactly_at_budget(self, client):
        """Test that an order equal to the budget is accepted."""
        candidate = first_candidates(client, 1)[0]
        response = client.post(
            "/api/restocking/orders",
            json=order_payload([candidate], budget=candidate["total_cost"]),
        )
        assert response.status_code == 201

    def test_create_order_unknown_sku(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": "NOPE-999", "quantity": 1}], "budget": 1000},
        )
        assert response.status_code == 400
        assert "unknown sku" in response.json()["detail"].lower()

    def test_create_order_duplicate_sku(self, client):
        """Test that the same SKU twice is rejected."""
        sku = first_candidates(client, 1)[0]["sku"]
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": sku, "quantity": 1}, {"sku": sku, "quantity": 2}],
                  "budget": 1_000_000},
        )
        assert response.status_code == 400

    def test_create_order_empty_items(self, client):
        """Test that an order with no items fails validation."""
        response = client.post("/api/restocking/orders", json={"items": [], "budget": 1000})
        assert response.status_code == 422

    @pytest.mark.parametrize("quantity", [0, -5])
    def test_create_order_non_positive_quantity(self, client, quantity):
        """Test that zero or negative quantities fail validation."""
        sku = first_candidates(client, 1)[0]["sku"]
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": sku, "quantity": quantity}], "budget": 1000},
        )
        assert response.status_code == 422

    def test_create_order_negative_budget(self, client):
        """Test that a negative budget fails validation."""
        sku = first_candidates(client, 1)[0]["sku"]
        response = client.post(
            "/api/restocking/orders",
            json={"items": [{"sku": sku, "quantity": 1}], "budget": -1},
        )
        assert response.status_code == 422

    def test_create_order_missing_body(self, client):
        """Test that a missing body fails validation."""
        assert client.post("/api/restocking/orders").status_code == 422


class TestSubmittedOrdersEndpoint:
    """Test suite for listing submitted restocking orders."""

    def test_empty_when_nothing_submitted(self, client):
        """Test that the list starts empty."""
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_submitted_order_appears_in_list(self, client):
        """Test that a placed order is returned."""
        created = client.post(
            "/api/restocking/orders", json=order_payload(first_candidates(client))
        ).json()

        data = client.get("/api/restocking/orders").json()
        assert len(data) == 1
        assert data[0]["id"] == created["id"]
        assert data[0]["lead_time_days"] == created["lead_time_days"]

    def test_newest_order_first(self, client):
        """Test that orders are listed newest first with unique numbers."""
        candidate = first_candidates(client, 1)
        first = client.post("/api/restocking/orders", json=order_payload(candidate)).json()
        second = client.post("/api/restocking/orders", json=order_payload(candidate)).json()

        data = client.get("/api/restocking/orders").json()
        assert [o["id"] for o in data] == [second["id"], first["id"]]
        assert first["order_number"] != second["order_number"]

    def test_submitted_orders_do_not_affect_existing_orders(self, client):
        """Test that placing an order leaves the regular orders and dashboard untouched."""
        orders_before = client.get("/api/orders").json()
        summary_before = client.get("/api/dashboard/summary").json()

        client.post("/api/restocking/orders", json=order_payload(first_candidates(client)))

        assert client.get("/api/orders").json() == orders_before
        assert client.get("/api/dashboard/summary").json() == summary_before
