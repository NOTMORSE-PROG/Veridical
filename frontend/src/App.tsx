// Route shell — placeholder until the real app shell/nav lands (V-014,
// screens 4a–4b). /gallery is the V-002 component review page.
import { BrowserRouter, Link, Route, Routes } from "react-router";
import { GalleryPage } from "./gallery/GalleryPage";

function IndexPage() {
  return (
    <main className="p-8">
      <h1 className="text-lg font-bold">VERIDICAL</h1>
      <p className="text-ink-soft">
        Frontend scaffold.{" "}
        <Link
          className="text-primary hover:text-primary-hover hover:underline"
          to="/gallery"
        >
          Component gallery
        </Link>
      </p>
    </main>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<IndexPage />} />
        <Route path="/gallery" element={<GalleryPage />} />
      </Routes>
    </BrowserRouter>
  );
}
