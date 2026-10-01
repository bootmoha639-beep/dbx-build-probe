import streamlit as st
st.title('probe')
try:
    import zzbuildprobe.build_evidence as bev
    st.json(bev.EVIDENCE)
except Exception as e:
    st.write('evidence load error: %s' % e)
