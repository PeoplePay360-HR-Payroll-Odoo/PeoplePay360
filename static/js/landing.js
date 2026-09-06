document.addEventListener("DOMContentLoaded", function () {

    // Close mobile navbar after clicking a section
    const navLinks = document.querySelectorAll(".nav-links .nav-link");

    const navbar = document.querySelector(".navbar-collapse");

    navLinks.forEach(function (link) {

        link.addEventListener("click", function () {

            if (navbar.classList.contains("show")) {

                const bsCollapse =
                    bootstrap.Collapse.getInstance(navbar);

                if (bsCollapse) {
                    bsCollapse.hide();
                }
            }

        });

    });

});