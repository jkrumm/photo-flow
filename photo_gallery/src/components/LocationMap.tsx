import React from 'react';
import {
  ComposableMap,
  Geographies,
  Geography,
  ZoomableGroup,
  Marker
} from 'react-simple-maps';

// Use local TOPO JSON file from public folder
const geoUrl = "/countries-50m.json";

interface LocationMapProps {
  latitude: number;
  longitude: number;
}

const LocationMap: React.FC<LocationMapProps> = ({ latitude, longitude }) => {
  // Higher zoom level for a very zoomed in view
  const zoom = 40;

  return (
    <div className="location-map">
      <ComposableMap
        projection="geoMercator"
        projectionConfig={{
          scale: 100, // Higher scale for more zoom
          center: [0, 0]
        }}
        style={{
          marginLeft: "auto",
          width: "40%",
          height: "100%",
          backgroundColor: "transparent"
        }}
      >
        <ZoomableGroup
          center={[longitude, latitude]}
          zoom={zoom}
          maxZoom={50}
          minZoom={1}
          disablepanning="true"
          disablezooming="true"
        >
          <Geographies geography={geoUrl}>
            {({ geographies }) =>
              geographies.map(geo => (
                <Geography
                  key={geo.rsmKey}
                  geography={geo}
                  style={{
                    default: {
                      fill: "transparent",
                      stroke: "#333",
                      strokeWidth: 0.15,
                      outline: "none",
                    },
                    hover: {
                      fill: "transparent",
                      stroke: "#333",
                      strokeWidth: 0.15,
                      outline: "none",
                    },
                    pressed: {
                      fill: "transparent",
                      stroke: "#333",
                      strokeWidth: 0.15,
                      outline: "none",
                    }
                  }}
                />
              ))
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
