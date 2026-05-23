---
layout: page
title: "Screenshots — ContaAutónomo user interface tour"
description: "Gallery of dashboard, invoicing, expense and module screens with click-to-zoom thumbnails for the ContaAutónomo app."
permalink: /screenshots/
---

A visual tour of ContaAutónomo. Pick any thumbnail to open the full-size screenshot in a lightbox; press <kbd>Escape</kbd> or click outside the image to close it and return to the gallery.

{% for group in site.data.screenshots.groups %}
<h2 id="{{ group.id }}">{{ group.title }}</h2>
<ul role="list" class="screenshot-grid">
  {% for item in group.items %}
  {% assign src = '/assets/img/screenshots/' | append: item.file | relative_url %}
  <li>
    <figure>
      <button type="button"
              data-lightbox-src="{{ src }}"
              data-lightbox-alt="{{ item.alt | escape }}"
              data-lightbox-caption="{{ item.caption | escape }}">
        <img src="{{ src }}" alt="{{ item.alt | escape }}" loading="lazy">
      </button>
      <figcaption>{{ item.caption }}</figcaption>
    </figure>
  </li>
  {% endfor %}
</ul>
{% endfor %}

<dialog id="lightbox" aria-labelledby="lightbox-caption">
  <img alt="">
  <figcaption id="lightbox-caption"></figcaption>
  <button type="button" data-lightbox-close aria-label="Close">×</button>
</dialog>

<script src="{{ '/assets/js/lightbox.js' | relative_url }}" defer></script>
