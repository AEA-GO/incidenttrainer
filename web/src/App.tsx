import { Route, Routes } from "react-router";
import { Game } from "./pages/Game";
import { Home } from "./pages/Home";
import { Lab } from "./pages/Lab";
import { Labs } from "./pages/Labs";
import { NotFound } from "./pages/NotFound";
import { Play } from "./pages/Play";
import { Results } from "./pages/Results";

// CloudFront sends every extension-less path to index.html (itr-main-spa-routing), so these
// all work as direct links. /api/* and /s/* are other CloudFront behaviours.
export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/play" element={<Play />} />
      <Route path="/labs" element={<Labs />} />
      <Route path="/labs/:labId" element={<Lab />} />
      <Route path="/game/:gameId" element={<Game />} />
      <Route path="/results/:gameId" element={<Results />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
