$(document).ready(function() {
      $('.menu a.item, .menu .link.item').click(
      function() {
        if(!$(this).hasClass('dropdown')) {
          $(this)
            .addClass('active')
            .closest('.ui.menu')
            .find('.item')
              .not($(this))
              .removeClass('active')
          ;
        }
      });
});
