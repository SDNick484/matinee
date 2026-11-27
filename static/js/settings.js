// Matinee — the Settings page (editors only): People, Locations and Lists, each manager drawn into its own section.
import { isAdmin, state } from "./core.js";
import { icon } from "./icons.js";
import { openPeopleManager } from "./people.js";
import { renderLocationsManager } from "./locations.js";
import { renderListsManager } from "./lists.js";

  // ================================================================= Settings
  function sectionHTML(id, title, iconName) {
    return `
      <section class="settings-section" id="settings-${id}" aria-labelledby="settings-${id}-title">
        <h2 class="stats-section-title" id="settings-${id}-title">${icon(iconName)} ${title}</h2>
        <div class="settings-body" id="settings-${id}-body"></div>
      </section>`;
  }

  async function renderSettings(container) {
    const seq = state.routeSeq;
    if (!isAdmin) {
      container.innerHTML = `
        <div class="empty-state">
          <h2>Editor Sign-in Required</h2>
          <p>Settings are only available to editors.</p>
          <a href="/admin#/settings" class="btn">Sign In as Editor</a>
        </div>`;
      return;
    }
    container.innerHTML = `
      <div class="settings-view">
        <div class="stats-header">
          <h1>Settings</h1>
          <p>The people, shelves and lists Matinee knows about.</p>
        </div>
        ${sectionHTML("people", "People", "people")}
        ${sectionHTML("locations", "Locations", "pin")}
        ${sectionHTML("lists", "Lists", "list")}
      </div>`;
    if (seq !== state.routeSeq) return;   // the user already left Settings
    const body = (id) => container.querySelector(`#settings-${id}-body`);
    await Promise.all([
      openPeopleManager(body("people")),
      renderLocationsManager(body("locations")),
      renderListsManager(body("lists")),
    ]);
  }

export { renderSettings };
