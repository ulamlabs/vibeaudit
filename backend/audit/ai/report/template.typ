// VibeAudit Report Template
// Variables injected by typst_builder.py:
//   repo_name, completed_at, risk_level, summary
// Report body (converted markdown) is appended after this template by _render_source.

#let severity-color(level) = {
  if level == "critical" { rgb("#c0392b") }
  else if level == "high" { rgb("#e67e22") }
  else if level == "medium" { rgb("#f1c40f") }
  else if level == "low" { rgb("#27ae60") }
  else { rgb("#7f8c8d") }
}

#set document(title: "VibeAudit Report — " + repo_name)
#set page(
  paper: "a4",
  margin: (top: 2.5cm, bottom: 2.5cm, left: 2.5cm, right: 2.5cm),
  header: align(right)[
    #text(size: 8pt, fill: luma(120))[VibeAudit — #repo_name]
  ],
  footer: align(center)[
    #context text(size: 8pt, fill: luma(120))[
      #counter(page).display("1 of 1", both: true)
    ]
  ],
)
#set text(font: "Libertinus Serif", size: 10pt)
#set heading(numbering: "1.")

// Cover page
#align(center)[
  #v(4cm)
  #text(size: 28pt, weight: "bold")[VibeAudit Report]
  #v(0.5cm)
  #text(size: 16pt, fill: luma(80))[#repo_name]
  #v(1cm)
  #text(size: 10pt, fill: luma(120))[Generated: #completed_at]
  #v(6cm)
]

#pagebreak()

// Table of contents
#outline(depth: 2)
#pagebreak()

// Report body
*Overall risk:* #text(fill: severity-color(risk_level), weight: "bold")[#upper(risk_level)]

#summary

// Converted report body appended here by typst_builder._render_source
