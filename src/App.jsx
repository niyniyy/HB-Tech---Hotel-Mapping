
import { useEffect, useState } from "react";
import "./App.css";

const API_BASE_URL = "http://localhost:8001";

/* ---------------- MOCK DATA FALLBACKS ---------------- */

const mockStatistics = {
  total_supplier_hotels: 224269,
  total_master_hotels: 1240,
  total_hotel_mappings: 18320,
  pending_queue_count: 5300,
  completed_count: 18000,
  manual_review_count: 3,
};

const mockMappingStatus = {
  Pending: 5300,
  Processing: 12,
  Completed: 18000,
  Failed: 4,
  ManualReview: 3,
};

const mockSupplierMatchingStats = [
  {
    supplier_name: "BookingCom",
    total_hotels: 75000,
    mapped_hotels: 64200,
    mapping_percentage: 85.6,
    auto_mapped: 60000,
    new_master: 2000,
    manual_mapped: 1800,
    manual_new_master: 400,
  },
  {
    supplier_name: "Cleartrip",
    total_hotels: 42000,
    mapped_hotels: 39000,
    mapping_percentage: 92.9,
    auto_mapped: 35000,
    new_master: 2500,
    manual_mapped: 1200,
    manual_new_master: 300,
  },
  {
    supplier_name: "GRNConnect",
    total_hotels: 51000,
    mapped_hotels: 46800,
    mapping_percentage: 91.8,
    auto_mapped: 42000,
    new_master: 2800,
    manual_mapped: 1600,
    manual_new_master: 400,
  },
];

const mockManualReviews = [
  {
    supplier_hotel_id: 1,
    supplier_name: "CleartripAPI",
    supplier_hotel_code: "316100",
    hotel_name: "IIDL Suites",
    address: "Plot No 4A, District Centre, Mayur Vihar, Delhi",
    city: "Delhi",
    country: "India",
    star_rating: 4,
    latitude: 28.594202,
    longitude: 77.299,
    suggested_master_hotel_id: "HB-70481815",
    suggested_master_hotel: "IIDL Suites",
    suggested_master_address:
      "Plot No 4A, District Centre, Mayur Vihar, New Delhi",
    suggested_master_latitude: 28.594202,
    suggested_master_longitude: 77.299002,
    rule_score: 86.5,
    ai_similarity: 0.91,
    decision_reason: "High name similarity and nearby coordinates",
  },
  {
    supplier_hotel_id: 2,
    supplier_name: "HummingBirdIndia",
    supplier_hotel_code: "134772",
    hotel_name: "Fraser Suites New Delhi",
    address: "Plot 4A, District Centre, Mayur Vihar, New Delhi",
    city: "Delhi",
    country: "India",
    star_rating: 2,
    latitude: 28.594177,
    longitude: 77.29907,
    suggested_master_hotel_id: "HB-70481815",
    suggested_master_hotel: "IIDL Suites",
    suggested_master_address:
      "Plot No 4A, District Centre, Mayur Vihar, New Delhi",
    suggested_master_latitude: 28.594202,
    suggested_master_longitude: 77.299002,
    rule_score: 78.4,
    ai_similarity: 0.82,
    decision_reason: "Same coordinates but different hotel name",
  },
  {
    supplier_hotel_id: 3,
    supplier_name: "Booking.com",
    supplier_hotel_code: "4565",
    hotel_name: "Hotel Vijay Elanza",
    address: "Coimbatore, Tamil Nadu",
    city: "Coimbatore",
    country: "India",
    star_rating: 4,
    latitude: 11.0168,
    longitude: 76.9558,
    suggested_master_hotel_id: "HB-858",
    suggested_master_hotel: "Vijay Elanza",
    suggested_master_address: "Coimbatore, Tamil Nadu",
    suggested_master_latitude: 11.0168,
    suggested_master_longitude: 76.9558,
    rule_score: 88.2,
    ai_similarity: 0.89,
    decision_reason: "Similar name and same location",
  },
];

const mockMasterHotels = [
  {
    master_hotel_id: "HB-70481815",
    hotel_name: "IIDL Suites",
    address: "Plot No 4A, District Centre, Mayur Vihar, New Delhi",
    city: "Delhi",
    country: "India",
    star_rating: 4.5,
    latitude: 28.594202,
    longitude: 77.299002,
  },
  {
    master_hotel_id: "HB-858",
    hotel_name: "Vijay Elanza",
    address: "Coimbatore, Tamil Nadu",
    city: "Coimbatore",
    country: "India",
    star_rating: 4,
    latitude: 11.0168,
    longitude: 76.9558,
  },
];

const mockMasterMappings = [
  {
    supplier_name: "CleartripAPI",
    supplier_hotel_id: "316100",
    hotel_name: "IIDL Suites",
    address: "Plot No 4A, District Centre, Mayur Vihar, Delhi",
    city: "Delhi",
    country: "India",
    star_rating: 4,
    latitude: 28.594202,
    longitude: 77.299,
    mapping_type: "AUTO",
    match_score: 94.5,
    is_manual_verified: false,
  },
  {
    supplier_name: "GRNConnect",
    supplier_hotel_id: "1394118",
    hotel_name: "IIDL Suites",
    address: "Plot 4A, District Centre, Mayur Vihar, New Delhi",
    city: "Delhi",
    country: "India",
    star_rating: 4,
    latitude: 28.594177,
    longitude: 77.29907,
    mapping_type: "MANUAL",
    match_score: 92.2,
    is_manual_verified: true,
  },
];

/* ---------------- APP ---------------- */

export default function App() {
  const [activePage, setActivePage] = useState("dashboard");

  return (
    <div className="app">
      <Header activePage={activePage} setActivePage={setActivePage} />

      {activePage === "dashboard" && <DashboardPage />}
      {activePage === "manual-review" && <ManualReviewPage />}
      {activePage === "master-hotels" && <MasterHotelsPage />}
      {activePage === "import" && <ImportPage />}
    </div>
  );
}

/* ---------------- HEADER / NAV ---------------- */

function Header({ activePage, setActivePage }) {
  return (
    <header className="app-header">
      <div>
        <h1>HB Hotel Mapping</h1>
        <p>
          Frontend UI for hotel import, matching, manual review, and master hotel mapping.
        </p>
      </div>

      <nav className="nav-tabs">
        <button
          className={activePage === "dashboard" ? "active" : ""}
          onClick={() => setActivePage("dashboard")}
        >
          Dashboard
        </button>

        <button
          className={activePage === "manual-review" ? "active" : ""}
          onClick={() => setActivePage("manual-review")}
        >
          Manual Review
        </button>

        <button
          className={activePage === "master-hotels" ? "active" : ""}
          onClick={() => setActivePage("master-hotels")}
        >
          Master Hotels
        </button>

        <button
          className={activePage === "import" ? "active" : ""}
          onClick={() => setActivePage("import")}
        >
          Import
        </button>
      </nav>
    </header>
  );
}

/* ---------------- DASHBOARD PAGE ---------------- */

function DashboardPage() {
  const [statistics, setStatistics] = useState(null);
  const [mappingStatus, setMappingStatus] = useState(null);
  const [supplierStats, setSupplierStats] = useState([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    loadDashboard();
  }, []);

  async function loadDashboard() {
    setLoading(true);
    setMessage("");

    try {
      const statsResponse = await fetch(`${API_BASE_URL}/api/v1/mapping/statistics`);
      if (!statsResponse.ok) throw new Error("Statistics API failed");
      const statsData = await statsResponse.json();
      setStatistics(statsData);
    } catch (error) {
      setStatistics(mockStatistics);
      setMessage("Using demo statistics because backend is not connected.");
    }

    try {
      const statusResponse = await fetch(`${API_BASE_URL}/api/v1/mapping/status`);
      if (!statusResponse.ok) throw new Error("Mapping status API failed");
      const statusData = await statusResponse.json();
      setMappingStatus(statusData);
    } catch (error) {
      setMappingStatus(mockMappingStatus);
    }

    setLoading(false);

    try {
  const supplierResponse = await fetch(
    `${API_BASE_URL}/api/v1/mapping/supplier-statistics`
  );

  if (!supplierResponse.ok) throw new Error("Supplier statistics API failed");

  const supplierData = await supplierResponse.json();

  setSupplierStats(
    Array.isArray(supplierData)
      ? supplierData
      : supplierData.suppliers || supplierData.results || supplierData.items || []
  );
} catch (error) {
  setSupplierStats(mockSupplierMatchingStats);
}
  }

  async function runMappingEngine() {
    setMessage("Starting mapping engine...");

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/mapping/run?limit=100&apply_decision=true`,
        {
          method: "POST",
        }
      );

      if (!response.ok) throw new Error("Run mapping failed");

      setMessage("Mapping engine started successfully.");
      loadDashboard();
    } catch (error) {
      setMessage("Demo: Mapping engine trigger clicked. Backend not connected.");
    }
  }

  return (
    <main className="page">
      <PageTitle
        title="Dashboard"
        subtitle="Overall statistics and queue status for the hotel mapping system."
      />

      {message && <Alert message={message} />}

      {loading ? (
        <Loading text="Loading dashboard..." />
      ) : (
        <>
          <section className="stats-grid">
            <StatCard
              title="Supplier Hotels"
              value={pick(statistics, [
                "total_supplier_hotels",
                "supplier_hotels",
                "totalSupplierHotels",
              ])}
            />
            <StatCard
              title="Master Hotels"
              value={pick(statistics, [
                "total_master_hotels",
                "master_hotels",
                "totalMasterHotels",
              ])}
            />
            <StatCard
              title="Hotel Mappings"
              value={pick(statistics, [
                "total_hotel_mappings",
                "hotel_mappings",
                "totalHotelMappings",
              ])}
            />
            <StatCard
              title="Pending Queue"
              value={pick(statistics, ["pending_queue_count", "pending_count", "pending"])}
            />
            <StatCard
              title="Completed"
              value={pick(statistics, ["completed_count", "completed"])}
            />
            <StatCard
              title="Manual Review"
              value={pick(statistics, [
                "manual_review",
                "manual_review_count",
                "manualReview",
                "ManualReview",
              ])}
            
            />
          </section>

          <section className="card">
  <div className="card-header">
    <div>
      <h2>Supplier-wise Matching Percentage</h2>
      <p>Percentage of supplier hotels mapped to master hotel records.</p>
    </div>
  </div>

  <SupplierMatchingBars suppliers={supplierStats} />
</section>

          <section className="card">
            <div className="card-header">
              <div>
                <h2>Queue Status</h2>
                <p>Current mapping queue counts grouped by status.</p>
              </div>

              <div className="button-row">
                <button className="btn-secondary" onClick={loadDashboard}>
                  Refresh
                </button>
                <button className="btn-primary" onClick={runMappingEngine}>
                  Run Mapping Engine
                </button>
              </div>
            </div>

            <div className="status-grid">
              {Object.entries(mappingStatus || {}).map(([key, value]) => (
                <div className="status-pill" key={key}>
                  <span>{key}</span>
                  <strong>{String(value)}</strong>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );


}

function SupplierMatchingBars({ suppliers }) {
  if (!suppliers || suppliers.length === 0) {
    return <EmptyState text="No supplier-wise matching data available." />;
  }

  return (
    <div className="supplier-dashboard">
      {suppliers.map((supplier, index) => {
        const supplierName =
          supplier.supplier_name ??
          supplier.supplierName ??
          supplier.name ??
          `Supplier ${index + 1}`;

        const totalHotels =
          supplier.total_hotels ??
          supplier.totalHotels ??
          0;

        const mappedHotels =
          supplier.mapped_hotels ??
          supplier.mappedHotels ??
          0;

        const autoMapped =
          supplier.auto_mapped ??
          supplier.autoMapped ??
          0;

        const newMaster =
          supplier.new_master ??
          supplier.newMaster ??
          0;

        const manualMapped =
          supplier.manual_mapped ??
          supplier.manualMapped ??
          0;

        const manualNewMaster =
          supplier.manual_new_master ??
          supplier.manualNewMaster ??
          0;

        let percentage =
          supplier.mapping_percentage ??
          supplier.mappingPercentage ??
          supplier.percentage ??
          0;

        if (
          (percentage === undefined || percentage === null) &&
          Number(totalHotels) > 0
        ) {
          percentage = (Number(mappedHotels) / Number(totalHotels)) * 100;
        }

        const safePercentage = Math.max(
          0,
          Math.min(100, Number(percentage || 0))
        );

        return (
          <div className="supplier-stat-card" key={`${supplierName}-${index}`}>
            <div className="supplier-stat-top">
              <div>
                <h3>{supplierName}</h3>
                <p>
                  {formatValue(mappedHotels)} mapped out of{" "}
                  {formatValue(totalHotels)} hotels
                </p>
              </div>

              <div className="supplier-percent">
                {safePercentage.toFixed(1)}%
              </div>
            </div>

            <div className="supplier-progress-track">
              <div
                className="supplier-progress-fill"
                style={{ width: `${safePercentage}%` }}
              />
            </div>

            <div className="supplier-metrics-grid">
              <div>
                <span>Auto mapped</span>
                <strong>{formatValue(autoMapped)}</strong>
              </div>

              <div>
                <span>New master</span>
                <strong>{formatValue(newMaster)}</strong>
              </div>

              <div>
                <span>Manual mapped</span>
                <strong>{formatValue(manualMapped)}</strong>
              </div>

              <div>
                <span>Manual new master</span>
                <strong>{formatValue(manualNewMaster)}</strong>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
/* ---------------- MANUAL REVIEW PAGE ---------------- */

function ManualReviewPage() {
  const [reviews, setReviews] = useState([]);
  const [selectedReview, setSelectedReview] = useState(null);
  const [search, setSearch] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [message, setMessage] = useState("");
  const [showReviewPopup, setShowReviewPopup] = useState(false);

  useEffect(() => {
    loadManualReviews();
  }, []);

  async function loadManualReviews() {
    setLoadingList(true);
    setMessage("");

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/manual-review?limit=100`);
      if (!response.ok) throw new Error("Manual review API failed");

      const data = await response.json();
      setReviews(Array.isArray(data) ? data : data.reviews || data.items || data.results || []);
    } catch (error) {
      setReviews(mockManualReviews);
      setMessage("Using demo manual review data because backend is not connected.");
    }

    setLoadingList(false);
  }

  async function viewReview(supplierHotelId) {
    setLoadingDetails(true);
    setMessage("");
    setShowReviewPopup(true);

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/manual-review/${supplierHotelId}`);
      if (!response.ok) throw new Error("Manual review details API failed");

      const data = await response.json();
      setSelectedReview(data);
    } catch (error) {
      const fallback = reviews.find(
        (item) => String(getSupplierHotelId(item)) === String(supplierHotelId)
      );
      setSelectedReview(fallback);
      setMessage("Using demo detail data because backend is not connected.");
    }

    setLoadingDetails(false);
  }

  function closeReviewPopup() {
    setShowReviewPopup(false);
    setSelectedReview(null);
  }

  async function approveReview(supplierHotelId) {
    await runManualReviewAction(
      `${API_BASE_URL}/api/v1/manual-review/${supplierHotelId}/approve`,
      "Suggested match approved successfully.",
      "Demo: Suggested match approved."
    );
  }

  async function createMaster(supplierHotelId) {
    await runManualReviewAction(
      `${API_BASE_URL}/api/v1/manual-review/${supplierHotelId}/create-master`,
      "New master hotel created successfully.",
      "Demo: New master hotel created."
    );
  }

  async function rejectReview(supplierHotelId) {
    await runManualReviewAction(
      `${API_BASE_URL}/api/v1/manual-review/${supplierHotelId}/reject`,
      "Review rejected and requeued successfully.",
      "Demo: Review rejected and requeued."
    );
  }

  async function runManualReviewAction(url, successMessage, demoMessage) {
    try {
      const response = await fetch(url, { method: "POST" });
      if (!response.ok) throw new Error("Action failed");

      setMessage(successMessage);
      setSelectedReview(null);
      setShowReviewPopup(false);
      loadManualReviews();
    } catch (error) {
      setMessage(demoMessage);
    }
  }

  const filteredReviews = reviews.filter((review) => {
    const text = [
      getSupplierHotelId(review),
      getSupplierName(review),
      getHotelName(review),
      getSuggestedMasterName(review),
      getDecisionReason(review),
      getAddress(review),
      getCity(review),
      getCountry(review),
      getLatitude(review),
      getLongitude(review),
      getStarRating(review),
    ]
      .join(" ")
      .toLowerCase();

    return text.includes(search.toLowerCase());
  });

  return (
    <main className="page">
      <PageTitle
        title="Manual Review"
        subtitle="Review borderline hotel matches and decide whether to approve, reject, or create a new master hotel."
      />

      {message && <Alert message={message} />}

      <section className="toolbar">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search by hotel name, supplier, address, city, coordinates..."
        />
        <button className="btn-secondary" onClick={loadManualReviews}>
          Refresh
        </button>
      </section>

      <section className="manual-review-wide">
        <div className="card table-card">
          <div className="card-header">
            <div>
              <h2>Hotels Waiting for Manual Review</h2>
              <p>{filteredReviews.length} records shown</p>
            </div>
          </div>

          {loadingList ? (
            <Loading text="Loading manual review records..." />
          ) : filteredReviews.length === 0 ? (
            <EmptyState text="No manual review records found." />
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Master Hotel ID</th>
                    <th>Provider Name</th>
                    <th>Provider Hotel ID</th>
                    <th>Hotel Name</th>
                    <th>Address</th>
                    <th>City</th>
                    <th>Country</th>
                    <th>Star</th>
                    <th>Latitude</th>
                    <th>Longitude</th>
                    <th>Rule Score</th>
                    <th>AI Similarity</th>
                    <th>Decision Reason</th>
                    <th>View</th>
                  </tr>
                </thead>

                <tbody>
                  {filteredReviews.map((review, index) => {
                    const supplierHotelId = getSupplierHotelId(review);

                    return (
                      <tr key={supplierHotelId || index}>
                        <td>{index + 1}</td>
                        <td>{getSuggestedMasterId(review)}</td>
                        <td>{getSupplierName(review)}</td>
                        <td>{getSupplierHotelCode(review)}</td>
                        <td>{getHotelName(review)}</td>
                        <td className="address-cell">{getAddress(review)}</td>
                        <td>{getCity(review)}</td>
                        <td>{getCountry(review)}</td>
                        <td>{getStarRating(review)}</td>
                        <td>{getLatitude(review)}</td>
                        <td>{getLongitude(review)}</td>
                        <td>
                          <ScoreBadge score={getRuleScore(review)} />
                        </td>
                        <td>{getAiSimilarity(review)}</td>
                        <td className="reason-cell">{getDecisionReason(review)}</td>
                        <td>
                          <button
                            className="btn-view"
                            onClick={() => viewReview(supplierHotelId)}
                          >
                            View
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {showReviewPopup && (
        <Modal title="Manual Review Hotel Information" onClose={closeReviewPopup}>
          {loadingDetails ? (
            <Loading text="Loading hotel information..." />
          ) : !selectedReview ? (
            <EmptyState text="No hotel details found." />
          ) : (
            <ReviewDetails
              review={selectedReview}
              approveReview={approveReview}
              createMaster={createMaster}
              rejectReview={rejectReview}
            />
          )}
        </Modal>
      )}
    </main>
  );
}

function ReviewDetails({ review, approveReview, createMaster, rejectReview }) {
  const supplierHotelId = getSupplierHotelId(review);

  return (
    <>
      <div className="popup-details-grid">
        <div className="details-section">
          <h3>Supplier Hotel Details</h3>
          <Detail label="Supplier" value={getSupplierName(review)} />
          <Detail label="Supplier Hotel ID" value={getSupplierHotelCode(review)} />
          <Detail label="Hotel Name" value={getHotelName(review)} />
          <Detail label="Address" value={getAddress(review)} />
          <Detail label="City" value={getCity(review)} />
          <Detail label="Country" value={getCountry(review)} />
          <Detail label="Star Rating" value={getStarRating(review)} />
          <Detail label="Latitude" value={getLatitude(review)} />
          <Detail label="Longitude" value={getLongitude(review)} />
        </div>

        <div className="details-section">
          <h3>Suggested Master Hotel</h3>
          <Detail label="Master Hotel ID" value={getSuggestedMasterId(review)} />
          <Detail label="Suggested Match" value={getSuggestedMasterName(review)} />
          <Detail label="Suggested Address" value={getSuggestedMasterAddress(review)} />
          <Detail label="Suggested City" value={getSuggestedMasterCity(review)} />
          <Detail label="Suggested Country" value={getSuggestedMasterCountry(review)} />
          <Detail label="Suggested Star Rating" value={getSuggestedMasterStar(review)} />
          <Detail label="Suggested Latitude" value={getSuggestedMasterLatitude(review)} />
          <Detail label="Suggested Longitude" value={getSuggestedMasterLongitude(review)} />
        </div>

        <div className="details-section">
          <h3>Scores and Reason</h3>
          <Detail label="Rule Score" value={getRuleScore(review)} />
          <Detail label="AI Similarity" value={getAiSimilarity(review)} />
          <Detail label="Decision Reason" value={getDecisionReason(review)} />
        </div>
      </div>

      <div className="details-actions">
        <button className="btn-approve" onClick={() => approveReview(supplierHotelId)}>
          Approve Suggested Match
        </button>

        <button className="btn-primary" onClick={() => createMaster(supplierHotelId)}>
          Create New Master Hotel
        </button>

        <button className="btn-reject" onClick={() => rejectReview(supplierHotelId)}>
          Reject / Requeue
        </button>
      </div>
    </>
  );
}

/* ---------------- MASTER HOTELS PAGE ---------------- */

function MasterHotelsPage() {
  const [query, setQuery] = useState("");
  const [providerHotelId, setProviderHotelId] = useState("");
  const [providerName, setProviderName] = useState("");
  const [hotelChainName, setHotelChainName] = useState("");
  const [propertyType, setPropertyType] = useState("");
  const [country, setCountry] = useState("");
  const [cityName, setCityName] = useState("");
  const [star, setStar] = useState(0);

  const [results, setResults] = useState([]);
  const [selectedMaster, setSelectedMaster] = useState(null);
  const [mappings, setMappings] = useState([]);
  const [showMasterPopup, setShowMasterPopup] = useState(false);

  const [message, setMessage] = useState("");
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [loadingDetails, setLoadingDetails] = useState(false);

  const providerOptions = [
    "BookingCom",
    "Cleartrip",
    "CleartripAPI",
    "GRN",
    "GRNConnect",
    "HummingBird",
    "HummingBirdIndia",
    "Sabre",
    "SabreAPI",
    "SabreGDS",
  ];

function getSearchText() {
  return (
    query ||
    providerHotelId ||
    hotelChainName ||
    cityName ||
    country ||
    providerName
  );
}

  async function searchMasterHotels() {
    const searchText = getSearchText();

    if (!searchText) {
      setMessage("Please enter a hotel name or filter value to search.");
      return;
    }

    setLoadingSearch(true);
    setMessage("");

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/master-hotels/search?q=${encodeURIComponent(
          searchText
        )}&limit=50`
      );

      if (!response.ok) throw new Error("Master hotel search failed");

      const data = await response.json();
      const baseResults = Array.isArray(data)
        ? data
        : data.results || data.items || [];

      const enrichedResults = await Promise.all(
        baseResults.map(async (hotel) => {
          const masterHotelId = getMasterHotelId(hotel);

          if (!masterHotelId || masterHotelId === "-") return hotel;

          try {
            const detailResponse = await fetch(
              `${API_BASE_URL}/api/v1/master-hotels/${masterHotelId}`
            );

            if (!detailResponse.ok) return hotel;

            const detailData = await detailResponse.json();

            return {
              ...hotel,
              ...detailData,
            };
          } catch (error) {
            return hotel;
          }
        })
      );

      setResults(enrichedResults);
    } catch (error) {
      setResults(mockMasterHotels);
      setMessage("Using demo master hotel search results because backend is not connected.");
    }

    setLoadingSearch(false);
  }

  async function openMasterPopup(masterHotelId) {
    setLoadingDetails(true);
    setMessage("");
    setShowMasterPopup(true);
    setMappings([]);

    try {
      const detailsResponse = await fetch(
        `${API_BASE_URL}/api/v1/master-hotels/${masterHotelId}`
      );

      if (!detailsResponse.ok) throw new Error("Master details failed");

      const detailsData = await detailsResponse.json();
      setSelectedMaster(detailsData);
    } catch (error) {
      const fallback = results.find(
        (hotel) => String(getMasterHotelId(hotel)) === String(masterHotelId)
      );

      setSelectedMaster(fallback || mockMasterHotels[0]);
      setMessage("Using demo master hotel details because backend is not connected.");
    }

    try {
      const mappingsResponse = await fetch(
        `${API_BASE_URL}/api/v1/master-hotels/${masterHotelId}/mappings`
      );

      if (!mappingsResponse.ok) throw new Error("Mappings failed");

      const mappingsData = await mappingsResponse.json();

      setMappings(
        Array.isArray(mappingsData)
          ? mappingsData
          : mappingsData.mapped_hotels || mappingsData.items || mappingsData.results || []
      );
    } catch (error) {
      setMappings(mockMasterMappings);
    }

    setLoadingDetails(false);
  }

  function resetFilters() {
    setQuery("");
    setProviderHotelId("");
    setProviderName("");
    setHotelChainName("");
    setPropertyType("");
    setCountry("");
    setCityName("");
    setStar(0);
    setResults([]);
    setMessage("");
  }

  return (
    <main className="page master-search-page">
      <PageTitle
        title="Master Hotels"
        subtitle="Search master hotels and view mapped supplier records."
      />

      {message && <Alert message={message} />}

      <section className="master-search-top">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Enter hotel name to search"
          onKeyDown={(event) => {
            if (event.key === "Enter") searchMasterHotels();
          }}
        />
      </section>

      <section className="master-filter-panel">
        <div className="master-filter-grid">
          <label>
            Provider Hotel Id:
            <input
              value={providerHotelId}
              onChange={(event) => setProviderHotelId(event.target.value)}
              placeholder="Provider Hotel Id"
            />
          </label>

        

          <label>
            Provider Name:
            <select
              value={providerName}
              onChange={(event) => setProviderName(event.target.value)}
            >
              <option value="">Select Provider Name</option>
              {providerOptions.map((provider) => (
                <option key={provider} value={provider}>
                  {provider}
                </option>
              ))}
            </select>
          </label>

          <label>
            Hotel Chain Name:
            <input
              value={hotelChainName}
              onChange={(event) => setHotelChainName(event.target.value)}
              placeholder="Search or Type Hotel Chain"
            />
          </label>

          <label>
            Property Type:
            <input
              value={propertyType}
              onChange={(event) => setPropertyType(event.target.value)}
              placeholder="Search or Type Category"
            />
          </label>

          <label>
            Country:
            <input
              value={country}
              onChange={(event) => setCountry(event.target.value)}
              placeholder="Search Country"
            />
          </label>

          <label>
            City Name:
            <input
              value={cityName}
              onChange={(event) => setCityName(event.target.value)}
              placeholder="City Name"
            />
          </label>

          <label className="star-filter">
            Star:
            <input
              type="range"
              min="0"
              max="5"
              step="0.5"
              value={star}
              onChange={(event) => setStar(event.target.value)}
            />
            <div className="star-scale">
              <span>0</span>
              <span>1</span>
              <span>2</span>
              <span>3</span>
              <span>4</span>
              <span>5</span>
            </div>
          </label>
        </div>

        <div className="master-filter-actions">
          <button className="btn-property-count" type="button">
            Get Property Count
          </button>

          <div className="master-action-buttons">
            <button className="btn-outline" onClick={resetFilters}>
              Reset
            </button>
            <button className="btn-search-blue" onClick={searchMasterHotels}>
              Search
            </button>
          </div>
        </div>
      </section>

      <section className="master-results-card">
        {loadingSearch ? (
          <Loading text="Searching master hotels..." />
        ) : results.length === 0 ? (
          <EmptyState text="Search for a master hotel to see results." />
        ) : (
          <div className="master-results-scroll">
            <table className="master-results-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>MastelHotel ID</th>
                  <th>Provider Name</th>
                  <th>Provider Hotel Id</th>
                  <th>Hotel Name</th>
                  <th>Address</th>
                  <th>Star</th>
                  <th>Lat</th>
                  <th>Long</th>
                  <th>View</th>
                  <th>Find Duplicate</th>
                </tr>
              </thead>

              <tbody>
                {results.map((hotel, index) => {
                  const masterId = getMasterHotelId(hotel);

                  return (
                    <tr key={masterId || index}>
                      <td>{index + 1}</td>
                      <td>{masterId}</td>
                      <td>{hotel.supplier_name ?? hotel.provider_name ?? "Master"}</td>
                      <td>
                        {hotel.supplier_hotel_id ??
                          hotel.provider_hotel_id ??
                          hotel.hotel_id ??
                          "-"}
                      </td>
                      <td>{getMasterHotelName(hotel)}</td>
                      <td className="address-cell">{getMasterAddress(hotel)}</td>
                      <td>{getMasterStar(hotel)}</td>
                      <td>{getMasterLatitude(hotel)}</td>
                      <td>{getMasterLongitude(hotel)}</td>
                      <td>
                        <button
                          className="icon-button"
                          onClick={() => openMasterPopup(masterId)}
                          title="View details"
                        >
                          👁
                        </button>
                      </td>
                      <td>
                        <button
                          className="icon-button"
                          onClick={() => openMasterPopup(masterId)}
                          title="Find duplicate"
                        >
                          ↗
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {showMasterPopup && (
        <Modal
          title="Master Hotel Information"
          onClose={() => {
            setShowMasterPopup(false);
            setSelectedMaster(null);
            setMappings([]);
          }}
        >
          {loadingDetails ? (
            <Loading text="Loading master hotel information..." />
          ) : !selectedMaster ? (
            <EmptyState text="No master hotel details found." />
          ) : (
            <div className="master-popup-content">
              <section className="master-popup-details">
                <Detail label="Master Hotel ID" value={getMasterHotelId(selectedMaster)} />
                <Detail label="Hotel Name" value={getMasterHotelName(selectedMaster)} />
                <Detail label="Address" value={getMasterAddress(selectedMaster)} />
                <Detail label="City" value={getMasterCity(selectedMaster)} />
                <Detail label="Country" value={getMasterCountry(selectedMaster)} />
                <Detail label="Star Rating" value={getMasterStar(selectedMaster)} />
                <Detail label="Latitude" value={getMasterLatitude(selectedMaster)} />
                <Detail label="Longitude" value={getMasterLongitude(selectedMaster)} />
              </section>

              <h3>Mapped Supplier Hotels</h3>

              {mappings.length === 0 ? (
                <EmptyState text="No supplier mappings found." />
              ) : (
                <div className="master-popup-table-scroll">
                  <table className="master-results-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Provider Name</th>
                        <th>Provider Hotel Id</th>
                        <th>Hotel Name</th>
                        <th>Address</th>
                        <th>City</th>
                        <th>Country</th>
                        <th>Star</th>
                        <th>Lat</th>
                        <th>Long</th>
                        <th>Mapping Type</th>
                        <th>Match Score</th>
                      </tr>
                    </thead>

                    <tbody>
                      {mappings.map((mapping, index) => (
                        <tr key={index}>
                          <td>{index + 1}</td>
                          <td>{mapping.supplier_name ?? mapping.supplierName ?? "-"}</td>
                          <td>
                            {mapping.supplier_hotel_id ??
                              mapping.supplierHotelId ??
                              mapping.provider_hotel_id ??
                              "-"}
                          </td>
                          <td>{mapping.hotel_name ?? mapping.hotelName ?? "-"}</td>
                          <td className="address-cell">
                            {mapping.address ?? mapping.supplier_address ?? "-"}
                          </td>
                          <td>{mapping.city ?? mapping.supplier_city ?? "-"}</td>
                          <td>{mapping.country ?? mapping.supplier_country ?? "-"}</td>
                          <td>{mapping.star_rating ?? mapping.starRating ?? "-"}</td>
                          <td>{getLatitude(mapping)}</td>
                          <td>{getLongitude(mapping)}</td>
                          <td>{mapping.mapping_type ?? mapping.mappingType ?? "-"}</td>
                          <td>
                            <ScoreBadge score={mapping.match_score ?? mapping.matchScore} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Modal>
      )}
    </main>
  );
}

/* ---------------- IMPORT PAGE ---------------- */

function ImportPage() {
  const [supplierName, setSupplierName] = useState("");
  const [file, setFile] = useState(null);
  const [importResult, setImportResult] = useState(null);
  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);

  async function importSupplierHotels(event) {
    event.preventDefault();
    setMessage("");
    setImportResult(null);

    if (!supplierName || !file) {
      setMessage("Please enter supplier name and select a CSV file.");
      return;
    }

    setUploading(true);

    try {
      const formData = new FormData();
      formData.append("supplier_name", supplierName);
      formData.append("file", file);

      const response = await fetch(`${API_BASE_URL}/api/v1/hotels/import`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) throw new Error("Import failed");

      const data = await response.json();
      setImportResult(data);
      setMessage("Supplier hotel import completed successfully.");
    } catch (error) {
      setImportResult({
        supplier_name: supplierName,
        filename: file.name,
        rows_received: "Demo",
        rows_inserted: "Demo",
        rows_skipped: "Demo",
        queue_records_created: "Demo",
      });
      setMessage("Demo: Import submitted. Backend not connected.");
    }

    setUploading(false);
  }

  return (
    <main className="page">
      <PageTitle
        title="Import Supplier Hotels"
        subtitle="Upload standardized supplier CSV files and import them into the hotel mapping database."
      />

      {message && <Alert message={message} />}

      <section className="import-layout">
        <div className="card">
          <h2>Upload CSV</h2>

          <form className="import-form" onSubmit={importSupplierHotels}>
            <label>
              Supplier Name
              <input
                value={supplierName}
                onChange={(event) => setSupplierName(event.target.value)}
                placeholder="Example: SabreAPI, CleartripAPI, Booking.com"
              />
            </label>

            <label>
              Supplier CSV File
              <input
                type="file"
                accept=".csv"
                onChange={(event) => setFile(event.target.files[0])}
              />
            </label>

            <button className="btn-primary" type="submit" disabled={uploading}>
              {uploading ? "Uploading..." : "Import Supplier Hotels"}
            </button>
          </form>
        </div>

        <div className="card">
          <h2>Import Summary</h2>

          {!importResult ? (
            <EmptyState text="Upload a file to see the import summary." />
          ) : (
            <div className="details-section">
              <Detail
                label="Supplier Name"
                value={importResult.supplier_name ?? importResult.supplierName}
              />
              <Detail label="Filename" value={importResult.filename} />
              <Detail
                label="Rows Received"
                value={importResult.rows_received ?? importResult.rowsReceived}
              />
              <Detail
                label="Rows Inserted"
                value={importResult.rows_inserted ?? importResult.rowsInserted}
              />
              <Detail
                label="Rows Skipped"
                value={importResult.rows_skipped ?? importResult.rowsSkipped}
              />
              <Detail
                label="Queue Records Created"
                value={
                  importResult.queue_records_created ??
                  importResult.queueRecordsCreated
                }
              />
            </div>
          )}
        </div>
      </section>
    </main>
  );
}

/* ---------------- SMALL UI COMPONENTS ---------------- */

function PageTitle({ title, subtitle }) {
  return (
    <section className="page-title">
      <h2>{title}</h2>
      <p>{subtitle}</p>
    </section>
  );
}

function StatCard({ title, value }) {
  return (
    <div className="stat-card">
      <h3>{title}</h3>
      <p>{value ?? "-"}</p>
    </div>
  );
}

function Alert({ message }) {
  return <div className="alert">{message}</div>;
}

function Loading({ text }) {
  return <div className="empty-state">{text}</div>;
}

function EmptyState({ text }) {
  return <div className="empty-state">{text}</div>;
}

function Detail({ label, value }) {
  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      <span>{formatValue(value)}</span>
    </div>
  );
}

function ScoreBadge({ score }) {
  if (score === undefined || score === null || score === "-") {
    return <span>-</span>;
  }

  const numericScore = Number(score);

  let className = "score-badge score-low";
  if (numericScore >= 90) className = "score-badge score-high";
  else if (numericScore >= 75) className = "score-badge score-medium";

  return <span className={className}>{formatValue(score)}</span>;
}

/* ---------------- DATA NORMALIZER HELPERS ---------------- */

function formatValue(value) {
  if (value === undefined || value === null || value === "") return "-";

  if (typeof value === "number") {
    return Number.isInteger(value) ? value : Number(value.toFixed(6));
  }

  return String(value);
}

function pick(object, keys) {
  if (!object) return "-";

  for (const key of keys) {
    if (object[key] !== undefined && object[key] !== null) {
      return object[key];
    }
  }

  return "-";
}

function getSupplierHotelId(item) {
  return item?.supplier_hotel_id ?? item?.supplierHotelId ?? item?.id ?? "-";
}

function getSupplierHotelCode(item) {
  return (
    item?.supplier_hotel_code ??
    item?.supplier_hotel_id_external ??
    item?.provider_hotel_id ??
    item?.providerHotelId ??
    item?.supplierHotelCode ??
    item?.supplier_hotel_id ??
    "-"
  );
}

function getSupplierName(item) {
  return (
    item?.supplier_name ??
    item?.supplierName ??
    item?.provider_name ??
    item?.providerName ??
    "-"
  );
}

function getHotelName(item) {
  return (
    item?.hotel_name ??
    item?.hotelName ??
    item?.supplier_hotel_name ??
    item?.supplierHotelName ??
    "-"
  );
}

function getAddress(item) {
  return (
    item?.address ??
    item?.supplier_address ??
    item?.supplierAddress ??
    item?.hotel_address ??
    "-"
  );
}

function getCity(item) {
  return item?.city ?? item?.supplier_city ?? item?.supplierCity ?? "-";
}

function getCountry(item) {
  return item?.country ?? item?.supplier_country ?? item?.supplierCountry ?? "-";
}

function getStarRating(item) {
  return item?.star_rating ?? item?.starRating ?? item?.star ?? "-";
}

function getLatitude(item) {
  return (
    item?.latitude ??
    item?.supplier_latitude ??
    item?.supplierLatitude ??
    item?.hotel_latitude ??
    item?.lat ??
    "-"
  );
}

function getLongitude(item) {
  return (
    item?.longitude ??
    item?.supplier_longitude ??
    item?.supplierLongitude ??
    item?.hotel_longitude ??
    item?.long ??
    item?.lng ??
    item?.lon ??
    "-"
  );
}

function getSuggestedMasterId(item) {
  return (
    item?.suggested_master_hotel_id ??
    item?.suggestedMasterHotelId ??
    item?.master_hotel_id ??
    item?.masterHotelId ??
    "-"
  );
}

function getSuggestedMasterName(item) {
  return (
    item?.suggested_master_hotel ??
    item?.suggested_master_hotel_name ??
    item?.suggestedMasterHotel ??
    item?.suggestedMasterHotelName ??
    item?.master_hotel_name ??
    "-"
  );
}

function getSuggestedMasterAddress(item) {
  return (
    item?.suggested_master_address ??
    item?.suggestedMasterAddress ??
    item?.master_address ??
    item?.master_hotel_address ??
    "-"
  );
}

function getSuggestedMasterCity(item) {
  return item?.suggested_master_city ?? item?.master_city ?? item?.masterCity ?? "-";
}

function getSuggestedMasterCountry(item) {
  return (
    item?.suggested_master_country ??
    item?.master_country ??
    item?.masterCountry ??
    "-"
  );
}

function getSuggestedMasterStar(item) {
  return (
    item?.suggested_master_star_rating ??
    item?.suggested_master_star ??
    item?.master_star_rating ??
    item?.masterStarRating ??
    "-"
  );
}

function getSuggestedMasterLatitude(item) {
  return (
    item?.suggested_master_latitude ??
    item?.suggestedMasterLatitude ??
    item?.master_latitude ??
    item?.masterLatitude ??
    "-"
  );
}

function getSuggestedMasterLongitude(item) {
  return (
    item?.suggested_master_longitude ??
    item?.suggestedMasterLongitude ??
    item?.master_longitude ??
    item?.masterLongitude ??
    "-"
  );
}

function getRuleScore(item) {
  return item?.rule_score ?? item?.ruleScore ?? item?.match_score ?? item?.matchScore ?? "-";
}

function getAiSimilarity(item) {
  return item?.ai_similarity ?? item?.aiSimilarity ?? item?.cosine_similarity ?? "-";
}

function getDecisionReason(item) {
  return item?.decision_reason ?? item?.decisionReason ?? item?.reason ?? "-";
}

function getMasterHotelId(item) {
  return item?.master_hotel_id ?? item?.masterHotelId ?? item?.id ?? "-";
}

function getMasterHotelName(item) {
  return item?.hotel_name ?? item?.hotelName ?? item?.master_hotel_name ?? item?.name ?? "-";
}

function getMasterAddress(item) {
  return item?.address ?? item?.master_address ?? item?.masterAddress ?? "-";
}

function getMasterCity(item) {
  return item?.city ?? item?.master_city ?? item?.masterCity ?? "-";
}

function getMasterCountry(item) {
  return item?.country ?? item?.master_country ?? item?.masterCountry ?? "-";
}

function getMasterStar(item) {
  return item?.star_rating ?? item?.starRating ?? item?.star ?? "-";
}

function getMasterLatitude(item) {
  return (
    item?.latitude ??
    item?.master_latitude ??
    item?.masterLatitude ??
    item?.lat ??
    "-"
  );
}

function getMasterLongitude(item) {
  return (
    item?.longitude ??
    item?.master_longitude ??
    item?.masterLongitude ??
    item?.lng ??
    item?.long ??
    item?.lon ??
    "-"
  );
}

/* ---------------- SMALL UI COMPONENTS ---------------- */
function Modal({ title, children, onClose }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

