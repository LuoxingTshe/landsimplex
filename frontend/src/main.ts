import { initMap } from "./map";
import { initPanel } from "./ui";

initMap();
initPanel().catch((err) => {
  console.error(err);
  document.getElementById("panel")!.innerHTML =
    `<div style="color: #d33; padding: 12px;">Failed to load: ${err}<br><br>Is the backend running at 127.0.0.1:8765?</div>`;
});
