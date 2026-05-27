"""
Plotly visualisations of synthea_fraud_graph findings.
Produces 4 HTML charts saved to /tmp/:
  1. Provider-ring network graph   (top 60 pairs by shared patients)
  2. Upcoding scatter              (encounter_count vs avg cost, sized by total_billed)
  3. High-volume providers bar     (top 20 by encounter count)
  4. Payer exposure pie            (unique patients per payer)

Open with: explorer.exe "\\\\wsl$\\Ubuntu\\tmp\\<file>.html"
"""
import oci, json, base64, os, oracledb, pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import networkx as nx
from dotenv import load_dotenv

load_dotenv('/mnt/c/Git_Repos/oci-ai-playground/.env')
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']

config = oci.config.from_file()
secrets_client = oci.secrets.SecretsClient(config)
bundle = secrets_client.get_secret_bundle(os.environ['GRAPHUSER_SECRET_OCID'])
creds = json.loads(base64.b64decode(bundle.data.secret_bundle_content.content).decode())
conn = oracledb.connect(user=creds['user_name'], password=creds['password'], dsn=creds['dsn'])
print(f"Connected as {creds['user_name']}")


def fetch(sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


# ── 1. Provider-ring network graph ────────────────────────────────────────────
print("Query 1: provider rings…")
rings = fetch("""
    SELECT pr1_id, pr2_id, COUNT(DISTINCT patient_id) AS shared_patients
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pr1 IS provider) <-[e1 IS had_encounter]- (p IS patient)
                                -[e2 IS had_encounter]-> (pr2 IS provider)
        WHERE pr1.provider_id < pr2.provider_id
        COLUMNS (pr1.provider_id AS pr1_id, pr2.provider_id AS pr2_id, p.id AS patient_id)
    )
    GROUP BY pr1_id, pr2_id
    HAVING COUNT(DISTINCT patient_id) > 50
    ORDER BY shared_patients DESC
    FETCH FIRST 60 ROWS ONLY
""")
print(f"  {len(rings)} ring pairs found")

# Build networkx graph
G = nx.Graph()
for _, row in rings.iterrows():
    # Shorten UUIDs to last 8 chars for labels
    u = row['PR1_ID'][-8:]
    v = row['PR2_ID'][-8:]
    G.add_edge(u, v, weight=row['SHARED_PATIENTS'])

pos = nx.spring_layout(G, seed=42, k=0.5)

# Node sizes proportional to degree (more connections = bigger)
degrees = dict(G.degree())
max_deg = max(degrees.values()) if degrees else 1

edge_x, edge_y, edge_hover = [], [], []
for u, v, data in G.edges(data=True):
    x0, y0 = pos[u]
    x1, y1 = pos[v]
    edge_x += [x0, x1, None]
    edge_y += [y0, y1, None]

node_x = [pos[n][0] for n in G.nodes()]
node_y = [pos[n][1] for n in G.nodes()]
node_labels = list(G.nodes())
node_sizes = [10 + 30 * degrees[n] / max_deg for n in G.nodes()]
node_hover = [f"Provider …{n}<br>Connections: {degrees[n]}" for n in G.nodes()]

fig1 = go.Figure()
fig1.add_trace(go.Scatter(
    x=edge_x, y=edge_y,
    mode='lines',
    line=dict(width=0.7, color='#aaa'),
    hoverinfo='none',
    name='Shared patients'
))
fig1.add_trace(go.Scatter(
    x=node_x, y=node_y,
    mode='markers+text',
    marker=dict(
        size=node_sizes,
        color=node_sizes,
        colorscale='Reds',
        showscale=True,
        colorbar=dict(title='Connections'),
        line=dict(width=1, color='#333'),
    ),
    text=node_labels,
    textposition='top center',
    textfont=dict(size=8),
    hovertext=node_hover,
    hoverinfo='text',
    name='Provider'
))
fig1.update_layout(
    title='Provider Ring Network — pairs sharing >50 patients<br><sub>Node size = number of ring connections</sub>',
    showlegend=False,
    xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
    yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
    height=700,
    plot_bgcolor='#f8f8f8',
)
path1 = '/tmp/fraud_01_provider_rings.html'
fig1.write_html(path1)
print(f"  Saved: {path1}")


# ── 2. Upcoding scatter ───────────────────────────────────────────────────────
print("Query 2: provider billing stats…")
billing = fetch("""
    SELECT provider_id, encounter_count,
           ROUND(total_billed, 0)       AS total_billed,
           ROUND(avg_per_encounter, 2)  AS avg_per_encounter
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pr IS provider)
        COLUMNS (
            pr.provider_id        AS provider_id,
            pr.encounter_count    AS encounter_count,
            pr.total_billed       AS total_billed,
            pr.avg_per_encounter  AS avg_per_encounter
        )
    )
    WHERE encounter_count >= 50
    ORDER BY avg_per_encounter DESC
""")
print(f"  {len(billing)} providers with ≥50 encounters")

# Flag top-5 upcoding suspects
billing['label'] = ''
top5 = billing.nlargest(5, 'AVG_PER_ENCOUNTER').index
billing.loc[top5, 'label'] = billing.loc[top5, 'PROVIDER_ID'].str[-8:]

# Mark outliers (avg > 2 std above mean)
mean_avg = billing['AVG_PER_ENCOUNTER'].mean()
std_avg  = billing['AVG_PER_ENCOUNTER'].std()
billing['suspect'] = billing['AVG_PER_ENCOUNTER'] > mean_avg + 2 * std_avg

fig2 = px.scatter(
    billing,
    x='ENCOUNTER_COUNT',
    y='AVG_PER_ENCOUNTER',
    size='TOTAL_BILLED',
    color='suspect',
    color_discrete_map={True: '#d62728', False: '#1f77b4'},
    hover_data={'PROVIDER_ID': True, 'ENCOUNTER_COUNT': True,
                'AVG_PER_ENCOUNTER': ':.2f', 'TOTAL_BILLED': ':,.0f', 'suspect': False},
    text='label',
    title='Upcoding Detection — Avg Cost per Encounter vs Volume<br>'
          '<sub>Red = >2 std above mean avg cost | Bubble size = total billed</sub>',
    labels={
        'ENCOUNTER_COUNT': 'Encounter Count',
        'AVG_PER_ENCOUNTER': 'Avg Cost per Encounter ($)',
        'suspect': 'Outlier'
    },
    height=600,
)
fig2.update_traces(textposition='top center', textfont_size=9)
fig2.add_hline(y=mean_avg + 2 * std_avg, line_dash='dash', line_color='red',
               annotation_text='Outlier threshold (mean + 2σ)',
               annotation_position='bottom right')
path2 = '/tmp/fraud_02_upcoding.html'
fig2.write_html(path2)
print(f"  Saved: {path2}")


# ── 3. High-volume providers bar ──────────────────────────────────────────────
print("Query 3: high-volume providers…")
highvol = fetch("""
    SELECT provider_id, encounter_count,
           ROUND(total_billed, 0) AS total_billed
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pr IS provider)
        COLUMNS (
            pr.provider_id      AS provider_id,
            pr.encounter_count  AS encounter_count,
            pr.total_billed     AS total_billed
        )
    )
    ORDER BY encounter_count DESC
    FETCH FIRST 20 ROWS ONLY
""")
highvol['short_id'] = highvol['PROVIDER_ID'].str[-12:]

fig3 = go.Figure()
fig3.add_trace(go.Bar(
    x=highvol['short_id'],
    y=highvol['ENCOUNTER_COUNT'],
    name='Encounters',
    marker_color='steelblue',
    hovertemplate='Provider: %{x}<br>Encounters: %{y:,}<extra></extra>',
))
fig3.add_trace(go.Bar(
    x=highvol['short_id'],
    y=highvol['TOTAL_BILLED'],
    name='Total Billed ($)',
    marker_color='coral',
    yaxis='y2',
    hovertemplate='Provider: %{x}<br>Total Billed: $%{y:,.0f}<extra></extra>',
))
fig3.update_layout(
    title='Top 20 Providers by Encounter Volume',
    barmode='group',
    xaxis=dict(title='Provider (last 12 chars of ID)', tickangle=-45),
    yaxis=dict(title='Encounter Count', side='left'),
    yaxis2=dict(title='Total Billed ($)', overlaying='y', side='right'),
    legend=dict(x=0.75, y=1),
    height=550,
)
path3 = '/tmp/fraud_03_highvolume.html'
fig3.write_html(path3)
print(f"  Saved: {path3}")


# ── 4. Payer exposure pie ─────────────────────────────────────────────────────
print("Query 4: payer exposure…")
payers = fetch("""
    SELECT payer_id, COUNT(DISTINCT patient_id) AS unique_patients
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pay IS payer) <-[i IS insured_by]- (p IS patient)
        COLUMNS (pay.payer_id AS payer_id, p.id AS patient_id)
    )
    GROUP BY payer_id
    ORDER BY unique_patients DESC
""")
payers['short_id'] = payers['PAYER_ID'].str[-12:]

fig4 = px.pie(
    payers,
    names='short_id',
    values='UNIQUE_PATIENTS',
    title='Payer Market Share — Unique Patients Insured',
    hover_data={'PAYER_ID': True},
    height=550,
)
fig4.update_traces(textposition='inside', textinfo='percent+label')
path4 = '/tmp/fraud_04_payers.html'
fig4.write_html(path4)
print(f"  Saved: {path4}")


conn.close()
print("\nAll charts saved. Open with:")
for p in [path1, path2, path3, path4]:
    wsl = p.replace('/tmp/', '\\\\wsl$\\Ubuntu\\tmp\\')
    print(f'  explorer.exe "{wsl}"')
