import React, { useState, useEffect } from 'react';
import {
  ComposableMap,
  Geographies,
  Geography,
  ZoomableGroup,
  Marker
} from 'react-simple-maps';
import { feature } from 'topojson-client';

// Use local TOPO JSON file from public folder
const geoUrl = "/countries-50m.json";

interface LocationMapProps {
  latitude: number;
  longitude: number;
}

const LocationMap: React.FC<LocationMapProps> = ({ latitude, longitude }) => {
  // Higher zoom level for a very zoomed in view
  const zoom = 40;
  const [relevantCountryId, setRelevantCountryId] = useState<string | null>(null);

  // Function to determine if a point is inside a polygon
  const isPointInPolygon = (point: [number, number], polygon: any) => {
    // Simple point-in-polygon algorithm
    const [x, y] = point;
    let inside = false;

    if (polygon.type === "Polygon") {
      for (const coordinates of polygon.coordinates) {
        for (let i = 0, j = coordinates.length - 1; i < coordinates.length; j = i++) {
          const [xi, yi] = coordinates[i];
          const [xj, yj] = coordinates[j];

          const intersect = ((yi > y) !== (yj > y)) && 
                            (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
          if (intersect) inside = !inside;
        }
      }
    } else if (polygon.type === "MultiPolygon") {
      for (const polygonPart of polygon.coordinates) {
        for (const coordinates of polygonPart) {
          for (let i = 0, j = coordinates.length - 1; i < coordinates.length; j = i++) {
            const [xi, yi] = coordinates[i];
            const [xj, yj] = coordinates[j];

            const intersect = ((yi > y) !== (yj > y)) && 
                              (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
            if (intersect) inside = !inside;
          }
        }
      }
    }

    return inside;
  };

  // Find the country containing the coordinates
  useEffect(() => {
    const fetchGeoData = async () => {
      try {
        const response = await fetch(geoUrl);
        const topojsonData = await response.json();
        const geojson = feature(topojsonData, topojsonData.objects.countries);

        // Find the country that contains the point
        const point: [number, number] = [longitude, latitude];

        for (const country of geojson.features) {
          if (isPointInPolygon(point, country.geometry)) {
            setRelevantCountryId(country.id);
            break;
          }
        }
      } catch (error) {
        console.error("Error loading or processing geo data:", error);
      }
    };

    fetchGeoData();
  }, [latitude, longitude]);

  // @ts-ignore
  // @ts-ignore
  return (
    <div className="location-map">
      <ComposableMap
        projection="geoMercator"
        projectionConfig={{
          scale: 65, // Higher scale for more zoom
          center: [0, 0]
        }}
        style={{
          marginLeft: "auto",
          height: "120px", // Set max height to 120px
          maxHeight: "120px",
          width: "300px",
          backgroundColor: "transparent",
          marginRight: "-100px" // Shift the map to the right
        }}
      >
        <ZoomableGroup
          center={[longitude, latitude]} // Center on the exact coordinates
          zoom={zoom}
          maxZoom={50}
          minZoom={1}
          disablepanning="true"
          disablezooming="true"
        >
          <Geographies geography={geoUrl}>
            {({ geographies }) =>
              geographies.map(geo => {
                const isRelevantCountry = geo.id === relevantCountryId;

                return (
                  <Geography
                    key={geo.rsmKey}
                    geography={geo}
                    style={{
                      default: {
                        fill: "#f0f0f0",
                        stroke: "#333",
                        strokeWidth: isRelevantCountry ? 0.07 : 0.03,
                        outline: "none",
                        opacity: isRelevantCountry ? 0.7 : 0.3
                      },
                      hover: {
                        fill: "#f0f0f0",
                        stroke: "#333",
                        strokeWidth: isRelevantCountry ? 0.07 : 0.03,
                        outline: "none",
                        opacity: isRelevantCountry ? 0.7 : 0.3
                      },
                      pressed: {
                        fill: isRelevantCountry ? "#f0f0f0" : "transparent",
                        stroke: isRelevantCountry ? "#333" : "transparent",
                        strokeWidth: isRelevantCountry ? 0.5 : 0,
                        outline: "none",
                        opacity: isRelevantCountry ? 1 : 0
                      }
                    }}
                  />
                );
              })
            }
          </Geographies>

          {/* Add a gray cross marker at the exact location */}
          <Marker coordinates={[longitude, latitude]}>
            <g
              stroke="#666"
              strokeWidth="0.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              transform="translate(-3, -3)"
            >
              {/* Cross shape - smaller */}
              <line x1="0" y1="3" x2="6" y2="3" />
              <line x1="3" y1="0" x2="3" y2="6" />
            </g>
          </Marker>
        </ZoomableGroup>
      </ComposableMap>
    </div>
  );
};

export default LocationMap;
