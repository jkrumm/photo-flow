# Photo Gallery

A responsive photo gallery built with Astro and Tailwind CSS that displays images from the `src/images` directory along with their metadata from `src/metadata.json`.

## Features

- Responsive design that works on all screen sizes
- Optimized image loading with Astro's image capabilities and Sharp
- Display of detailed image metadata (camera details, settings, date taken, etc.)
- Star rating display for images
- Support for high-quality JPG images from Fuji camera
- Clean, responsive grid layout that adapts to different screen sizes

## Getting Started

### Prerequisites

- Node.js (v16 or later)
- npm or yarn

### Installation

1. Clone this repository
2. Install dependencies:

```bash
npm install
# or
yarn
```

3. Start the development server:

```bash
npm run dev
# or
yarn dev
```

4. Open your browser and navigate to `http://localhost:4321`

## Adding Your Photos

1. Place your high-quality JPG images in the `src/images` directory
2. Update the `src/metadata.json` file with information about your images
3. The gallery will automatically display all images listed in the metadata file
4. Images will be displayed sorted by date taken, with the most recent photos at the top
5. For best results, include titles in the metadata or use descriptive filenames as they will be used as alt text

## Using the Gallery

### Viewing Images
- Images are displayed in a responsive grid layout (1 column on mobile, 2 on medium screens, 3 on large screens)
- Each image is displayed in a card with its metadata
- Metadata shown includes:
  - Camera details (make, model, ISO, aperture, shutter speed, focal length)
  - Image details (date taken, dimensions, rating, description if available)
- Star ratings are displayed visually for each image
- The gallery is fully responsive and adapts to different screen sizes

## Building for Production

```bash
npm run build
# or
yarn build
```

The built site will be in the `dist` directory, ready to be deployed.

## Validation

To check for any issues in the project:

```bash
npm run check
# or
yarn check
```

## Customization

- Modify the grid layout in `src/components/ImageGallery.astro`
- Adjust the card styling in `src/components/ImageCard.astro`
- Edit the layout styling in `src/layouts/Layout.astro`
- Customize Tailwind settings in `tailwind.config.mjs`

## Project Structure

```
src/
├── components/
│   ├── ImageCard.astro    # Component for displaying individual images
│   └── ImageGallery.astro # Component for displaying the gallery grid
├── images/                # Directory containing all the images
├── layouts/
│   └── Layout.astro       # Main layout component
├── pages/
│   └── index.astro        # Main page that displays the gallery
├── utils/
│   └── images.ts          # Utility functions for working with images
└── metadata.json          # Metadata for all images
```

## Technical Details

- Images are optimized using Sharp through Astro's built-in image optimization
- The gallery uses Astro's Image component for optimal image rendering
- Images are displayed with responsive sizing based on the viewport
- The gallery is fully responsive with different layouts for mobile, tablet, and desktop
