# PICKS PRO

Panel estático para consultar oportunidades calculadas desde cuotas reales de The Odds API. Esta primera versión es una base transparente: la probabilidad estimada es consenso de mercado sin margen, no una predicción independiente.

## Configuración

1. En GitHub abre **Settings → Secrets and variables → Actions → New repository secret** y crea el secreto **ODDS_API_KEY**. Nunca pegues la clave en archivos ni en el código de la página.
2. Abre **Actions → Update PICKS PRO data → Run workflow** para la primera actualización. Después se ejecuta cada seis horas.
3. Habilita GitHub Pages en **Settings → Pages**, selecciona despliegue desde la rama **main** y la carpeta raíz **/**.
4. La página carga **data/picks.json** de forma pública. El secreto solo lo usa GitHub Actions.

## Cálculo

- Probabilidad implícita: 1 / cuota decimal.
- Probabilidad estimada: promedio de probabilidades de casas normalizadas para quitar el margen, en el mismo mercado y línea.
- Edge: probabilidad estimada menos probabilidad implícita de la mejor cuota disponible.
- Confianza: señal heurística basada en cuántas casas reportan el resultado y cuánto difieren. No es una probabilidad de acierto validada.
- Stake: cuarto de Kelly, limitado a 1 unidad (1% del bankroll asumido). No representa recomendación personalizada.
- **NO BET** si el edge es menor de 3%, la confianza menor de 55% o no hay varias casas para comparar.

El generador consulta los deportes activos disponibles; el volumen de solicitudes depende de deportes, mercados y regiones de la cuenta de The Odds API. Ajusta ODDS_REGIONS y el horario en el workflow según tu cuota.

No hay datos ficticios en el JSON inicial. Esta herramienta es informativa; apuestas implican riesgo y no hay ganancias garantizadas.
