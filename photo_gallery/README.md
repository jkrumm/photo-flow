# Photo Gallery

A responsive photo gallery built with Astro, React, and Tailwind CSS.

## Features

- Responsive design that works on all screen sizes
- Optimized image loading with Astro's image capabilities
- Clean presentation without borders, maximizing screen space
- Support for high-quality JPG images from Fuji camera

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

1. Place your high-quality JPG images in the `public/images` directory
2. Update the `public/metadata.json` file with information about your images
3. The gallery will automatically display all images listed in the metadata file
4. Images will be displayed sorted by date taken, with the most recent photos at the top
5. For best results, include titles in the metadata or use descriptive filenames as they will be used as alt text

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

- Adjust the maximum width in `tailwind.config.mjs` (currently set to 1680px)
- Modify padding values in `tailwind.config.mjs` and `src/components/Gallery.tsx`
- Edit the gallery styling in `src/styles/global.css`

## Technical Details

- Images are displayed at the optimal resolution for the user's device
- Lazy loading is implemented for all images except the first one
- The gallery is fully responsive with different padding for mobile and desktop
