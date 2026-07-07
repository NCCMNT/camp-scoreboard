import streamlit as st

# --- PAGE CONFIG ---
st.set_page_config(page_title="Camp Leaderboard", page_icon="🏕️", layout="wide")

# --- CUSTOM CSS (For the Big Screen Look) ---
st.html("""
<style>
    /* Style the big screen team cards */
    .st-key-blue-team {
        background: linear-gradient(135deg, #1e3c72, #2a5298);
        color: white; padding: 20px; border-radius: 15px; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }
    .st-key-red-team {
        background: linear-gradient(135deg, #8b0000, #dc143c);
        color: white; padding: 20px; border-radius: 15px; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }
    h2 { font-family: 'Arial Black', sans-serif; }
</style>
""")

# --- MOCK DATABASE (Session State) ---
# This simulates your database until you connect Google Sheets
if "scores" not in st.session_state:
    st.session_state.scores = {"Blue Owls": 1200, "Red Foxes": 950}
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

# --- APP ROUTING ---
# Use the sidebar to switch between the Big Screen and the Admin panel
view = st.sidebar.radio("Navigation", ["📺 Leaderboard (Big Screen)", "📱 Counselor Login"])

# --- VIEW 1: BIG SCREEN LEADERBOARD ---
if view == "📺 Leaderboard (Big Screen)":
    st.markdown("<h1 style='text-align: center;'>🏕️ Camp Leaderboard</h1>", unsafe_allow_html=True)
    st.write("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        with st.container(key="blue-team"):
            st.markdown("## 🔵 Blue Owls")
            st.markdown(f"### {st.session_state.scores['Blue Owls']} pts")
            
    with col2:
        with st.container(key="red-team"):
            st.markdown("## 🔴 Red Foxes")
            st.markdown(f"### {st.session_state.scores['Red Foxes']} pts")

# --- VIEW 2: COUNSELOR ADMIN PANEL ---
elif view == "📱 Counselor Login":
    st.title("Counselor Access")
    
    # PIN CHECK
    if not st.session_state.authenticated:
        pin_input = st.text_input("Enter Camp PIN", type="password")
        if st.button("Login"):
            if pin_input == st.secrets["CAMP_PIN"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect PIN.")
    
    # ADMIN CONTROLS (Only shows if authenticated)
    if st.session_state.authenticated:
        st.success("Logged in successfully.")
        
        st.write("### Update Scores")
        # Blue Team Controls
        col1, col2, col3 = st.columns([2, 1, 1])
        col1.write("**Blue Owls**")
        if col2.button("+10", key="blue_add"):
            st.session_state.scores["Blue Owls"] += 10
            st.rerun()
        if col3.button("-10", key="blue_sub"):
            st.session_state.scores["Blue Owls"] -= 10
            st.rerun()

        # Red Team Controls
        col4, col5, col6 = st.columns([2, 1, 1])
        col4.write("**Red Foxes**")
        if col5.button("+10", key="red_add"):
            st.session_state.scores["Red Foxes"] += 10
            st.rerun()
        if col6.button("-10", key="red_sub"):
            st.session_state.scores["Red Foxes"] -= 10
            st.rerun()
            
        if st.button("Logout"):
            st.session_state.authenticated = False
            st.rerun()