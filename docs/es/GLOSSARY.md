# Glosario de localización (es-419)

Este glosario registra cada decisión de terminología de la interfaz en español del dashboard de forkfix y de `README.es.md`. Las cadenas viven en `app/i18n/es.json`; si cambias un término aquí, cámbialo también allí.

## Variante y estilo

- **Variante:** español de Latinoamérica, siguiendo la guía de estilo de Microsoft para español (México): <https://aka.ms/spanish-mexico-styleguide>. En el HTML se usa `lang="es-419"` y los números y fechas se formatean con la configuración regional `es-MX`.
- **Tratamiento:** **tú** en toda la interfaz y la documentación. La guía de Microsoft recomienda el tuteo para software. Ejemplos: "Toma los ACU comprometidos…", "Agrega la etiqueta…".
- **Mayúsculas:** solo la primera palabra y los nombres propios, igual que en la interfaz en inglés ("Buscar issues ahora", no "Buscar Issues Ahora").
- **Botones:** verbo en infinitivo que dice lo que pasa ("Buscar issues ahora", "Ver registro sin procesar").
- **Fuera de alcance:** los comentarios de GitHub, los títulos de los issues, la evidencia que escribe Devin y los registros del servidor se quedan en inglés. En el HTML van marcados con `lang="en"`.

## Términos que no se traducen

Los ingenieros los dicen en inglés y aparecen así en GitHub y en Devin.

| Término | Uso en español | Nota |
| --- | --- | --- |
| CVE | "3 CVE alcanzables" | Sin plural con -s: "los CVE". |
| PR | "PR abierto", "PR abiertos" | Masculino: "el PR". |
| ACU | "55 ACU", "ACU comprometidos" | Unidad de cómputo de Devin. Masculino y sin -s. |
| Devin | "sesión de Devin" | Nombre propio. |
| playbook | "el playbook de triaje" | Masculino. |
| DeepWiki | "DeepWiki" | Nombre propio. |
| issue | "issues detectados", "Abrir issue en GitHub" | Masculino ("el issue"), como en GitHub en español. Se prefiere a "incidencia" porque GitHub no lo traduce. |
| pipeline | "Pipeline" (pestaña), "el pipeline" | Masculino. |
| webhook | "se aceptó el webhook" | Masculino. |
| upstream | "Sincronización con upstream" | El repositorio original del que viene el fork. |
| fork | "el fork" | Masculino. |
| changelog | "PR del changelog" | Masculino. |
| DEMO / LIVE | "Modo DEMO" | Nombres de modo de la aplicación; coinciden con `APP_MODE`. |

## Términos traducidos

| Inglés | Español | Por qué |
| --- | --- | --- |
| Overview | Resumen | Pestaña principal; "Vista general" es más larga y no aporta. |
| Sessions | Sesiones | |
| Cost | Costo | "Costo" en México y Latinoamérica ("coste" es de España). |
| Seen (stage) | Detectado | La etapa en la que la búsqueda encuentra el issue. |
| Triage | Triaje | Se usa en seguridad y en medicina en español. |
| Route (stage) | Decisión | "Enrutamiento" es largo y raro; la etapa es una decisión: corregir, pedir a una persona o cerrar. |
| Routed to fix | Enviado a corrección | |
| Fix (stage) | Corrección | |
| Needs a human | Necesita a una persona | Más claro que "intervención manual". Plural: "Necesitan a una persona". |
| Reachable / Not reachable | Alcanzable / No alcanzable | Traducción directa de "reachability". |
| Unknown (verdict) | Sin determinar | "Desconocido" suena a error; aquí significa que el triaje no pudo decidir. |
| Confidence high / medium / low | Confianza alta / media / baja | |
| Gates | Condiciones | Feature flags, opciones de configuración y permisos que tienen que estar activos para llegar al código. |
| Feature flag | feature flag | Se queda en inglés cuando nombra un flag concreto de Superset. |
| Reachable in a default install | Alcanzable en una instalación predeterminada | "Predeterminado" según la guía de Microsoft (no "por defecto"). |
| Sweep (noun) | Búsqueda | La tarea periódica que busca issues con la etiqueta. "Barrido" es literal y poco claro. |
| Run sweep now | Buscar issues ahora | El botón dice lo que pasa. |
| Label | Etiqueta | Igual que GitHub en español. |
| ACU cap (per session) | Límite de ACU | Lo máximo que puede gastar una sesión. |
| ACU ceiling (global) | Tope de ACU | Lo máximo que puede reservar todo el pipeline. "Límite" y "tope" nunca se intercambian. |
| Committed ACUs | ACU comprometidos | Reservados contra el tope, aunque todavía no se hayan gastado. |
| Metered ACUs | ACU medidos | Lo que Devin reporta como consumido. |
| Not reported by plan | El plan no lo reporta | Se muestra cuando una sesión terminada reporta 0 ACU medidos. Nunca se muestra "$0". |
| Waiting for budget | Esperando presupuesto | |
| Automation rate | Tasa de automatización | |
| Median time to PR | Mediana de tiempo hasta el PR | |
| Stage timeline | Cronología de etapas | |
| Triage evidence | Evidencia del triaje | |
| History | Historial | |
| Show raw log | Ver registro sin procesar | "Log" se traduce como "registro" según la guía de Microsoft. |
| Devin mode (`devin_mode`) | Modo de Devin | El valor técnico se muestra tal cual cuando no hay traducción. |
| Org default | Predeterminado de la organización | |
| Finished (session) | Terminada | Femenino por "sesión". |
| Demo data | Datos de demostración | Insignia visible en modo DEMO. |
| Updated now | Actualizado ahora | |
| Skip to content | Ir al contenido | |

## Formato

- **Números:** `Intl.NumberFormat("es-MX")`: coma de miles y punto decimal (1,234 y 2.5 ACU), como en México.
- **Fechas:** `Intl.DateTimeFormat("es-MX")`: "2 oct, 08:31". El servidor genera el texto con los mismos patrones para que la página se vea igual sin JavaScript.
- **Tiempo relativo:** `Intl.RelativeTimeFormat("es-MX")`: "hace 5 minutos".
- **Porcentajes:** "60 %", con espacio de no separación antes del signo, como lo formatea `Intl` en es-MX.
- **Duraciones:** "2 s", "3 min 05 s", "1 h 04 min".
