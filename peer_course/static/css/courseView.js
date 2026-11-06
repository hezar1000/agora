function toggleSessionCancel(checkbox) {
    const row = checkbox.closest('tr');  
    const sessionId = checkbox.getAttribute("data-session-id");
    const isCanceled = checkbox.checked; 
    const courseId = window.courseId;
    const csrfToken = window.csrf_token;
    const nextSessionDate = window.nextSessionDate;
    const nextSessionTime = window.nextSessionTime;
  
    fetch(`/course/${courseId}/update_session_cancel/`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken
        },
        body: JSON.stringify({
            session_id: sessionId,
            cancel_next_session: isCanceled
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status !== "success") {
            alert("Failed to update session status: " + data.message);
            checkbox.checked = !isCanceled;
        } else {
            if (isCanceled) {
              row.classList.add('session-row-cancelled');
            } else {
              row.classList.remove('session-row-cancelled');
            }
  
            const nextContainer = document.getElementById("next-session-container");
            if (nextContainer && nextContainer.getAttribute("data-session-id") === sessionId) {
                const infoElem = document.getElementById("next-session-info");
                const cancelButton = document.getElementById("cancel_lecture");
                if (isCanceled) {
                    infoElem.innerHTML =
                      `<del>Next Session: ${nextSessionDate} at ${nextSessionTime}</del>`;
                    cancelButton.innerText = "Undo cancel";
                    cancelButton.setAttribute("data-canceled", "true");
                } else {
                    infoElem.innerHTML =
                      `Next Session: ${nextSessionDate} at ${nextSessionTime}`;
                    cancelButton.innerText = "Cancel this session";
                    cancelButton.setAttribute("data-canceled", "false");
                }
            }
        }
    })
    .catch(error => {
        alert("Error: " + error);
        checkbox.checked = !isCanceled;
    });
  }
  